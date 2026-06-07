#!/usr/bin/env python3
"""Performer tempo, ensemble structure, and MRT2 adapter orchestration."""

from __future__ import annotations

import argparse
import collections
import os
import queue
import threading
import time
from dataclasses import dataclass

import cv2
import mediapipe as mp
import rtmidi
from pythonosc import dispatcher, osc_server, udp_client

from .cameras import discover_cameras, open_camera, print_camera_list, resolve_camera
from .core import (
    EnsembleController,
    InteractionMode,
    NodDetector,
    TransportState,
    intent_to_mrt2,
)


NOSE_TIP_IDX = 1
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(PROJECT_ROOT, "models")

# ---------------------------------------------------------------------------
# Stability helpers
# ---------------------------------------------------------------------------

class NodConfidence:
    """Confidence of BPM estimate from nod intervals (0–1). Fades after nods complete."""

    def __init__(self):
        self._intervals: list[float] = []
        self._completed_at: float | None = None
        self._fade_duration = 3.0

    def record_interval(self, interval: float) -> None:
        self._intervals.append(interval)

    def mark_complete(self) -> None:
        self._completed_at = time.monotonic()

    def confidence(self) -> float:
        if len(self._intervals) < 2:
            return 0.0
        mean = sum(self._intervals) / len(self._intervals)
        variance = sum((x - mean) ** 2 for x in self._intervals) / len(self._intervals)
        cv = (variance ** 0.5) / mean if mean > 0 else 1.0
        return max(0.0, min(1.0, 1.0 - cv * 5))

    def visible(self) -> bool:
        """Show during nod phase and for fade_duration seconds after completion."""
        if self._completed_at is None:
            return True
        return (time.monotonic() - self._completed_at) < self._fade_duration


class PerformerIntensity:
    """Track musician's playing intensity from recent note velocities."""

    def __init__(self, window: int = 8):
        self._velocities: collections.deque[float] = collections.deque(maxlen=window)

    def record_note(self, velocity: int) -> None:
        self._velocities.append(float(velocity))

    def intensity(self) -> float:
        """0–1, based on mean velocity of recent notes."""
        if not self._velocities:
            return 0.0
        return min(1.0, sum(self._velocities) / len(self._velocities) / 127.0)

    def energy_floor(self) -> float:
        """Soft minimum energy the conductor should feel resistance below."""
        i = self.intensity()
        if i > 0.7:
            return 0.4   # playing hard → background can't go too quiet
        if i > 0.4:
            return 0.2
        return 0.0       # playing softly → conductor has full range


# Style → background tint colour for performer HUD (BGR)
STYLE_TINTS = {
    0: (30, 100, 180),   # Warm Acoustic → warm orange
    1: (60, 60, 60),     # Minimal Pulse → neutral grey
    2: (180, 200, 40),   # Bright Electronic → cyan
    3: (120, 40, 20),    # Dark Cinematic → deep blue
    4: (40, 80, 180),    # Percussive Experimental → purple
}


def draw_nod_hud(
    frame: "np.ndarray",
    nod_confidence: "NodConfidence",
    nods_done: int,
    nods_needed: int,
    style_index: int,
    conductor_energy: float,
    energy_floor: float,
) -> None:
    h, w = frame.shape[:2]

    # --- Background tint from conductor style (subtle, bottom strip) ---
    tint = STYLE_TINTS.get(style_index, (40, 40, 40))
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 36), (w, h), tint, -1)
    cv2.addWeighted(overlay, 0.25, frame, 0.75, 0, frame)

    # --- Nod progress / confidence (disappears after fade) ---
    if nod_confidence.visible():
        panel_y = h - 36
        confidence = nod_confidence.confidence()
        bar_w = 200
        bx = 14

        if nods_done < nods_needed:
            # Dots showing nod progress
            for i in range(nods_needed):
                cx = bx + i * 22
                cy = panel_y + 18
                color = (0, 220, 80) if i < nods_done else (60, 60, 60)
                cv2.circle(frame, (cx, cy), 7, color, -1)
            cv2.putText(frame, f"NOD {nods_done}/{nods_needed}",
                        (bx + nods_needed * 22 + 10, panel_y + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
        else:
            # Confidence bar, fades out
            elapsed = time.monotonic() - nod_confidence._completed_at
            alpha = max(0.0, 1.0 - elapsed / nod_confidence._fade_duration)
            bar_color_base = (0, 220, 80) if confidence > 0.75 else (0, 180, 255) if confidence > 0.4 else (0, 100, 220)
            bar_color = tuple(int(c * alpha) for c in bar_color_base)
            text_color = tuple(int(c * alpha) for c in (200, 200, 200))
            cv2.rectangle(frame, (bx, panel_y + 8), (bx + bar_w, panel_y + 22), (40, 40, 40), -1)
            fill = int(confidence * bar_w)
            if fill > 0:
                cv2.rectangle(frame, (bx, panel_y + 8), (bx + fill, panel_y + 22), bar_color, -1)
            cv2.putText(frame, f"BPM confidence  {int(confidence * 100)}%",
                        (bx + bar_w + 10, panel_y + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, text_color, 1)

    # --- Conductor energy floor hint (right side, only when active) ---
    if energy_floor > 0.0 and conductor_energy < energy_floor + 0.05:
        hint = "↑ playing hard"
        cv2.putText(frame, hint, (w - 160, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 180, 255), 1)
DRUM_CHANNEL = 9
KICK, SNARE, HI_HAT = 36, 38, 42
MIDI_CLOCKS_PER_BEAT = 24
SEMANTIC_FIELDS = (
    "energy",
    "follow",
    "pulse",
    "adventure",
    "style_commitment",
)


@dataclass(frozen=True)
class ControlEvent:
    kind: str
    value: float | int | str | None = None
    received_at: float = 0.0


class MidiOutputs:
    """MIDI paths consumed by Reaper and the hosted MRT2 AU."""

    def __init__(self):
        self.reaper = rtmidi.MidiOut()
        self.reaper.open_virtual_port("MusicianClock")
        self.mrt2_notes = rtmidi.MidiOut()
        self.mrt2_notes.open_virtual_port("GestureInstrument")
        self._lock = threading.Lock()
        print("[MIDI] MusicianClock and GestureInstrument opened.")

    def clock(self) -> None:
        with self._lock:
            self.reaper.send_message([0xF8])

    def start_reaper(self) -> None:
        with self._lock:
            self.reaper.send_message([0xFA])

    def drum_hit(self, note: int, velocity: int) -> None:
        with self._lock:
            self.reaper.send_message([0x90 | DRUM_CHANNEL, note, velocity])
            self.reaper.send_message([0x80 | DRUM_CHANNEL, note, 0])

    def forward_note(self, message: list[int]) -> None:
        with self._lock:
            self.mrt2_notes.send_message(message)

    def close(self) -> None:
        with self._lock:
            self.reaper.send_message([0xFC])
        del self.reaper
        del self.mrt2_notes


class SharedClock(threading.Thread):
    def __init__(self, outputs: MidiOutputs):
        super().__init__(daemon=True)
        self.outputs = outputs
        self.bpm = 120.0
        self.running = False
        self.active = True
        self._pulse = 0
        self._condition = threading.Condition()

    def set_bpm(self, bpm: float) -> None:
        with self._condition:
            self.bpm = bpm
            self._condition.notify_all()

    def start_at_bar_head(self) -> None:
        with self._condition:
            self._pulse = 0
            self.running = True
            self.outputs.start_reaper()
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self.running = False

    def shutdown(self) -> None:
        with self._condition:
            self.active = False
            self._condition.notify_all()
        self.join(timeout=2.0)

    def run(self) -> None:
        deadline = time.monotonic()
        while True:
            with self._condition:
                while self.active and not self.running:
                    self._condition.wait(timeout=0.1)
                    deadline = time.monotonic()
                if not self.active:
                    return
                bpm = self.bpm
            self.outputs.clock()
            if self._pulse % MIDI_CLOCKS_PER_BEAT == 0:
                beat = (self._pulse // MIDI_CLOCKS_PER_BEAT) % 4
                self.outputs.drum_hit(HI_HAT, 70)
                if beat == 0:
                    self.outputs.drum_hit(KICK, 112)
                elif beat in (1, 3):
                    self.outputs.drum_hit(SNARE, 96)
            self._pulse += 1
            deadline += 60.0 / (bpm * MIDI_CLOCKS_PER_BEAT)
            time.sleep(max(0.0, deadline - time.monotonic()))


class PerformerMidiInput(threading.Thread):
    SKIP_NAMES = ("GestureInstrument", "MusicianClock")

    def __init__(self, outputs: MidiOutputs, requested_port: str | None):
        super().__init__(daemon=True)
        self.outputs = outputs
        self.active = False
        self.midiin = rtmidi.MidiIn()
        candidates = [
            (index, name)
            for index, name in enumerate(self.midiin.get_ports())
            if not any(skip in name for skip in self.SKIP_NAMES)
            and (not requested_port or requested_port.lower() in name.lower())
        ]
        if not candidates:
            print("[MIDI IN] No performer keyboard; MRT2 note prompt is disabled.")
            return
        index, name = candidates[0]
        self.midiin.open_port(index)
        self.midiin.ignore_types(sysex=True, timing=True, active_sense=True)
        self.active = True
        print(f"[MIDI IN] Performer notes: {name}")
        self.start()

    def run(self) -> None:
        while self.active:
            incoming = self.midiin.get_message()
            if incoming is None:
                time.sleep(0.001)
                continue
            message, _delta = incoming
            status = message[0] & 0xF0 if message else 0
            if status in (0x80, 0x90):
                self.outputs.forward_note(message)
                if status == 0x90 and len(message) > 2 and message[2] > 0:
                    self._on_note_on(time.monotonic())

    def _on_note_on(self, t: float) -> None:
        pass  # patched from main() after construction

    def close(self) -> None:
        self.active = False
        if self.is_alive():
            self.join(timeout=1.0)
        self.midiin.close_port()


class Mrt2MockAdapter:
    """Legacy OSC mock used to inspect conductor-to-MRT2 mappings."""

    def __init__(self, port: int):
        self.client = udp_client.SimpleUDPClient("127.0.0.1", port)
        self._last_parameters = None
        print(f"[MRT2 MOCK] OSC monitor expected at 127.0.0.1:{port}")

    def prepare(self) -> None:
        self.client.send_message("/mrt2/action/prepare", 1)
        self.client.send_message("/mrt2/volume", -60.0)
        self.client.send_message("/mrt2/bypass", 0)

    def start(self) -> None:
        self.client.send_message("/mrt2/action/start", 1)
        self.client.send_message("/mrt2/volume_ramp", [0.0, 0.12])

    def normal_stop(self, bar_duration: float) -> None:
        self.client.send_message("/mrt2/action/stop_queued", 1)
        self.client.send_message("/mrt2/volume_ramp", [-60.0, bar_duration])

    def hold(self, enabled: bool) -> None:
        self.client.send_message("/mrt2/action/hold", int(enabled))

    def emergency_stop(self) -> None:
        self.client.send_message("/mrt2/volume", -60.0)
        self.client.send_message("/mrt2/bypass", 1)

    def update(self, intent) -> None:
        parameters = intent_to_mrt2(intent)
        if parameters == self._last_parameters:
            return
        self.client.send_message("/mrt2/temperature", parameters.temperature)
        self.client.send_message("/mrt2/top_k", parameters.top_k)
        self.client.send_message("/mrt2/cfg_musiccoca", parameters.cfg_musiccoca)
        self.client.send_message("/mrt2/cfg_notes", parameters.cfg_notes)
        self.client.send_message("/mrt2/cfg_drums", parameters.cfg_drums)
        self.client.send_message("/mrt2/style", parameters.style_index)
        self.client.send_message("/mrt2/section", parameters.section_index)
        self._last_parameters = parameters


class Mrt2AuAdapter:
    """Control an MRT2 AU instance hosted on a Reaper track."""

    PARAMETER_INDEX = {
        "temperature": 1,
        "top_k": 2,
        "cfg_musiccoca": 4,
        "cfg_notes": 5,
        "cfg_drums": 49,
    }

    def __init__(self, port: int, track: int, fx: int):
        self.client = udp_client.SimpleUDPClient("127.0.0.1", port)
        self.track = track
        self.fx = fx
        self._last_parameters = None
        print(
            f"[MRT2 AU] Reaper track {track}, FX {fx}, "
            f"OSC 127.0.0.1:{port}"
        )

    @staticmethod
    def _normalized(value: float, minimum: float, maximum: float) -> float:
        return min(1.0, max(0.0, (value - minimum) / (maximum - minimum)))

    def _parameter(self, index: int, value: float) -> None:
        self.client.send_message(
            f"/track/{self.track}/fx/{self.fx}/fxparam/{index}/value",
            value,
        )

    def _bypass(self, enabled: bool) -> None:
        self.client.send_message(
            f"/track/{self.track}/fx/{self.fx}/bypass",
            int(enabled),
        )

    def prepare(self) -> None:
        self._bypass(False)

    def start(self) -> None:
        self._bypass(False)

    def normal_stop(self, _bar_duration: float) -> None:
        self._bypass(True)

    def hold(self, _enabled: bool) -> None:
        pass

    def emergency_stop(self) -> None:
        self._bypass(True)

    def update(self, intent) -> None:
        parameters = intent_to_mrt2(intent)
        if parameters == self._last_parameters:
            return
        values = {
            "temperature": self._normalized(parameters.temperature, 0.0, 3.0),
            "top_k": self._normalized(parameters.top_k, 1.0, 1024.0),
            "cfg_musiccoca": self._normalized(parameters.cfg_musiccoca, 0.0, 5.0),
            "cfg_notes": self._normalized(parameters.cfg_notes, 0.0, 5.0),
            "cfg_drums": self._normalized(parameters.cfg_drums, 0.0, 5.0),
        }
        for name, value in values.items():
            self._parameter(self.PARAMETER_INDEX[name], value)
        self._last_parameters = parameters


class StructuralScheduler(threading.Thread):
    """Commit structural actions independently of camera frame rate."""

    def __init__(self, adapter, events: queue.Queue[ControlEvent]):
        super().__init__(daemon=True)
        self.adapter = adapter
        self.events = events
        self.active = True
        self.action: str | None = None
        self.deadline: float | None = None
        self.duration = 0.0
        self._condition = threading.Condition()
        self.start()

    def schedule(self, action: str, deadline: float, duration: float = 0.0) -> None:
        with self._condition:
            self.action = action
            self.deadline = deadline
            self.duration = duration
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self.action = None
            self.deadline = None
            self._condition.notify_all()

    def shutdown(self) -> None:
        with self._condition:
            self.active = False
            self._condition.notify_all()
        self.join(timeout=2.0)

    def run(self) -> None:
        while True:
            with self._condition:
                while self.active and self.deadline is None:
                    self._condition.wait()
                if not self.active:
                    return
                delay = self.deadline - time.monotonic()
                if delay > 0:
                    self._condition.wait(timeout=delay)
                    continue
                action = self.action
                duration = self.duration
                self.action = None
                self.deadline = None

            if action == "start":
                self.adapter.start()
                self.events.put(
                    ControlEvent("boundary_start", received_at=time.monotonic())
                )
            elif action == "stop":
                self.adapter.normal_stop(duration)
                fade_started_at = time.monotonic()
                self.events.put(
                    ControlEvent(
                        "stop_fade_started",
                        duration,
                        fade_started_at,
                    )
                )
                self.schedule("stop_complete", fade_started_at + duration)
            elif action == "stop_complete":
                self.events.put(
                    ControlEvent("boundary_stop", received_at=time.monotonic())
                )


class ReaperOsc:
    GO_TO_PROJECT_START = 40042
    TRANSPORT_PLAY = 1007
    TRANSPORT_STOP = 1016

    def __init__(self, port: int):
        self.client = udp_client.SimpleUDPClient("127.0.0.1", port)

    def action(self, command_id: int) -> None:
        self.client.send_message(f"/action/{command_id}", 1.0)

    def tempo(self, bpm: float) -> None:
        self.client.send_message("/tempo/raw", bpm)

    def play(self) -> None:
        self.action(self.TRANSPORT_PLAY)

    def stop(self) -> None:
        self.action(self.TRANSPORT_STOP)

    def start_project(self, bpm: float) -> None:
        """Start the prepared Reaper drum arrangement at the fifth nod."""
        self.stop()
        self.action(self.GO_TO_PROJECT_START)
        self.tempo(bpm)
        self.play()


def start_control_server(events: queue.Queue[ControlEvent], port: int):
    osc_dispatcher = dispatcher.Dispatcher()

    def put(kind, value=None):
        events.put(ControlEvent(kind, value, time.monotonic()))

    for action in ("start", "hold", "resume", "stop", "emergency_stop"):
        osc_dispatcher.map(
            f"/conductor/action/{action}",
            lambda _address, *_args, name=action: put(name),
        )
    for field in SEMANTIC_FIELDS:
        osc_dispatcher.map(
            f"/conductor/{field}",
            lambda _address, value, name=field: put(name, float(value)),
        )
    for field in ("style", "section"):
        osc_dispatcher.map(
            f"/conductor/{field}",
            lambda _address, value, name=field: put(name, int(value)),
        )
    osc_dispatcher.map(
        "/conductor/mode",
        lambda _address, value: put("mode", str(value)),
    )
    osc_dispatcher.map(
        "/conductor/tracking",
        lambda _address, value: put("tracking", bool(value)),
    )

    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", port), osc_dispatcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[LOCAL OSC] Conductor intentions at 127.0.0.1:{port}")
    return server


def create_face_landmarker():
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    model = os.path.join(MODEL_DIR, "face_landmarker.task")
    return mp_vision.FaceLandmarker.create_from_options(
        mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
        )
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera",
        default="select",
        help="camera index, or 'select' to choose from detected cameras",
    )
    parser.add_argument("--list-cameras", action="store_true")
    parser.add_argument("--control-port", type=int, default=9000)
    parser.add_argument("--feedback-port", type=int, default=9002)
    parser.add_argument("--mrt2-backend", choices=("au", "mock"), default="au")
    parser.add_argument("--mrt2-port", type=int, default=9100)
    parser.add_argument("--mrt2-track", type=int, default=2)
    parser.add_argument("--mrt2-fx", type=int, default=1)
    parser.add_argument("--reaper-port", type=int, default=8000)
    parser.add_argument("--midi-port")
    parser.add_argument("--smoothing", type=float, default=0.65)
    parser.add_argument("--nods-to-start", type=int, default=5)
    parser.add_argument("--nods-to-lock", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_cameras:
        print_camera_list(discover_cameras())
        return
    camera_index = resolve_camera(args.camera, "performer")
    events: queue.Queue[ControlEvent] = queue.Queue()
    controller = EnsembleController(
        nods_to_start=args.nods_to_start,
        nods_to_lock=args.nods_to_lock,
        smoothing=args.smoothing,
    )
    outputs = MidiOutputs()
    clock = SharedClock(outputs)
    clock.start()
    performer = PerformerMidiInput(outputs, args.midi_port)
    if args.mrt2_backend == "mock":
        mrt2 = Mrt2MockAdapter(args.mrt2_port)
    else:
        mrt2 = Mrt2AuAdapter(
            port=args.reaper_port,
            track=args.mrt2_track,
            fx=args.mrt2_fx,
        )
    scheduler = StructuralScheduler(mrt2, events)

    def _note_on_callback(t: float) -> None:
        pass  # velocity patched separately

    performer._on_note_on = _note_on_callback

    # Also intercept velocity for intensity tracking
    _orig_forward = outputs.forward_note

    def _forward_with_intensity(message: list[int]) -> None:
        _orig_forward(message)
        if (message[0] & 0xF0) == 0x90 and len(message) > 2 and message[2] > 0:
            performer_intensity.record_note(message[2])

    outputs.forward_note = _forward_with_intensity

    # Listen to conductor energy/style from feedback port so performer HUD can show tint
    _conductor_osc = dispatcher.Dispatcher()

    def _set_conductor_energy(_addr, value):
        nonlocal _conductor_energy
        _conductor_energy = float(value)

    def _set_conductor_style(_addr, value):
        nonlocal _conductor_style
        _conductor_style = int(value)

    _conductor_osc.map("/conductor/energy", _set_conductor_energy)
    _conductor_osc.map("/conductor/style", _set_conductor_style)
    _conductor_listen = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 9003), _conductor_osc)
    threading.Thread(target=_conductor_listen.serve_forever, daemon=True).start()
    reaper = ReaperOsc(args.reaper_port)
    feedback = udp_client.SimpleUDPClient("127.0.0.1", args.feedback_port)
    server = start_control_server(events, args.control_port)
    face = create_face_landmarker()
    nod_detector = NodDetector()
    cap = open_camera(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open performer camera {camera_index}")

    frame_timestamp_ms = 0
    nod_confidence = NodConfidence()
    performer_intensity = PerformerIntensity()
    _last_nod_time: float | None = None
    _conductor_energy: float = 0.5
    _conductor_style: int = 0
    print(
        f"[READY] Performer nods {args.nods_to_start} times to start; "
        f"tempo locks after nod {args.nods_to_lock}."
    )
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            now = time.monotonic()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            frame_timestamp_ms += 33
            result = face.detect_for_video(image, frame_timestamp_ms)
            if result.face_landmarks:
                nose = result.face_landmarks[0][NOSE_TIP_IDX]
                detected, _ = nod_detector.update(nose.y)
                if detected:
                    previous_nod_count = controller.nod_count
                    was_waiting = controller.state.transport == TransportState.WAITING
                    state = controller.record_nod(now)
                    nod_was_accepted = controller.nod_count != previous_nod_count
                    if nod_was_accepted:
                        clock.set_bpm(state.bpm)
                        reaper.tempo(state.bpm)
                        if _last_nod_time is not None:
                            nod_confidence.record_interval(now - _last_nod_time)
                        _last_nod_time = now
                    if nod_was_accepted and was_waiting and state.transport == TransportState.READY:
                        nod_confidence.mark_complete()
                        clock.start_at_bar_head()
                        reaper.start_project(state.bpm)
                        print(
                            f"[REAPER] Start nod {args.nods_to_start}: "
                            f"tempo {state.bpm:.1f} BPM, "
                            "project returned to 1.1.00, playback started."
                        )
                    if controller.nod_count == args.nods_to_lock and previous_nod_count < args.nods_to_lock:
                        print(
                            f"[TEMPO] Locked at {state.bpm:.1f} BPM after "
                            f"nod {args.nods_to_lock}; later nods are ignored."
                        )
                cv2.circle(
                    frame,
                    (int(nose.x * frame.shape[1]), int(nose.y * frame.shape[0])),
                    7,
                    (0, 220, 255),
                    -1,
                )

            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                if event.kind == "start":
                    before = controller.state.transport
                    state = controller.request_start(event.received_at)
                    if before != state.transport:
                        mrt2.prepare()
                        scheduler.schedule("start", state.scheduled_at)
                elif event.kind == "hold":
                    controller.request_hold()
                    mrt2.hold(True)
                elif event.kind == "resume":
                    controller.request_resume()
                    mrt2.hold(False)
                elif event.kind == "stop":
                    state = controller.request_stop(event.received_at)
                    if state.transport == TransportState.STOP_QUEUED:
                        scheduler.schedule(
                            "stop",
                            state.scheduled_at,
                            controller.clock.bar_duration,
                        )
                elif event.kind == "emergency_stop":
                    controller.emergency_stop()
                    scheduler.cancel()
                    mrt2.emergency_stop()
                elif event.kind == "mode":
                    controller.set_mode(str(event.value))
                elif event.kind == "tracking":
                    previous = controller.state.transport
                    controller.set_tracking(bool(event.value))
                    if previous == TransportState.ACTIVE and not bool(event.value):
                        mrt2.hold(True)
                elif event.kind in SEMANTIC_FIELDS:
                    controller.update_intent(**{event.kind: float(event.value)})
                elif event.kind == "style":
                    controller.update_intent(style_index=int(event.value))
                elif event.kind == "section":
                    controller.update_intent(section_index=int(event.value))
                elif event.kind == "stop_fade_started":
                    controller.begin_stop_fade(
                        event.received_at,
                        float(event.value),
                    )
                elif event.kind in ("boundary_start", "boundary_stop"):
                    controller.tick(event.received_at)

            state = controller.state
            mrt2.update(state.intent)

            beat = controller.clock.beat_number(now) if state.transport != TransportState.WAITING else 0
            remaining = controller.clock.beats_until(now, state.scheduled_at)
            feedback.send_message("/feedback/state", state.transport.value)
            feedback.send_message("/feedback/mode", state.mode.value)
            feedback.send_message("/feedback/bpm", state.bpm)
            feedback.send_message("/feedback/beat", beat)
            feedback.send_message("/feedback/countdown", -1.0 if remaining is None else remaining)
            feedback.send_message("/feedback/tracking", int(state.tracking_ok))

            cv2.rectangle(frame, (0, 0), (frame.shape[1], 78), (0, 0, 0), -1)
            line1 = f"{state.transport.value}  BPM {state.bpm:.1f}  BEAT {beat or '-'}"
            tempo_status = "TEMPO LOCKED" if controller.tempo_locked else "TEMPO LEARNING"
            line2 = (
                f"MODE {state.mode.value.upper()}  "
                f"NODS {controller.nod_count}/{args.nods_to_lock}  {tempo_status}"
            )
            cv2.putText(frame, line1, (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (90, 255, 120), 2)
            cv2.putText(frame, line2, (14, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 2)

            energy_floor = performer_intensity.energy_floor()
            feedback.send_message("/performer/energy_floor", energy_floor)
            draw_nod_hud(
                frame,
                nod_confidence=nod_confidence,
                nods_done=controller.nod_count,
                nods_needed=args.nods_to_start,
                style_index=_conductor_style,
                conductor_energy=_conductor_energy,
                energy_floor=energy_floor,
            )
            cv2.imshow("Performer Tempo and Ensemble State", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                controller.reset()
                clock.stop()
                scheduler.cancel()
                mrt2.emergency_stop()
                reaper.stop()
    finally:
        cap.release()
        cv2.destroyAllWindows()
        face.close()
        server.shutdown()
        server.server_close()
        performer.close()
        scheduler.shutdown()
        clock.shutdown()
        outputs.close()


if __name__ == "__main__":
    main()
