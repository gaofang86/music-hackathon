#!/usr/bin/env python3
"""iPhone-camera conductor UI with calibration and three interaction modes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
import os
import threading
import time
import urllib.request

import cv2
import mediapipe as mp
import numpy as np
from pythonosc import dispatcher, osc_server, udp_client

from .core import InteractionMode, clamp


WRIST = 0
FINGER_PAIRS = ((8, 6), (12, 10), (16, 14), (20, 18))
FACE_TOP, FACE_CHIN = 10, 152
UPPER_LIP, LOWER_LIP = 13, 14
LEFT_MOUTH, RIGHT_MOUTH = 61, 291
LEFT_EYE, RIGHT_EYE = 386, 159
LEFT_BROW, RIGHT_BROW = 105, 334
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_WRIST, RIGHT_WRIST = 15, 16
POSE_POINTS = (11, 12, 23, 24, 15, 16)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(PROJECT_ROOT, "models")
PROFILE_DIR = os.path.join(PROJECT_ROOT, "profiles")
STYLE_NAMES = (
    "Warm Acoustic",
    "Minimal Pulse",
    "Bright Electronic",
    "Dark Cinematic",
    "Percussive Experimental",
)


def ensure_model(filename: str, url: str) -> str:
    os.makedirs(MODEL_DIR, exist_ok=True)
    path = os.path.join(MODEL_DIR, filename)
    if not os.path.exists(path):
        urllib.request.urlretrieve(url, path)
    return path


def finger_extended(landmarks, tip: int, pip: int) -> bool:
    return landmarks[tip].y < landmarks[pip].y


def open_palm(landmarks) -> bool:
    return sum(finger_extended(landmarks, tip, pip) for tip, pip in FINGER_PAIRS) >= 3


def closed_fist(landmarks) -> bool:
    return all(not finger_extended(landmarks, tip, pip) for tip, pip in FINGER_PAIRS)


def movement(
    current: list[tuple[float, float]],
    previous: list[tuple[float, float]] | None,
) -> float:
    if previous is None or len(current) != len(previous):
        return 0.0
    return float(np.mean([
        math.hypot(x - old_x, y - old_y)
        for (x, y), (old_x, old_y) in zip(current, previous)
    ]))


@dataclass
class RawSignals:
    tracking: bool = False
    energy: float = 0.0
    x: float = 0.5
    y: float = 0.5
    spread: float = 0.0
    brow: float = 0.0
    mouth: float = 0.0
    smile: float = 0.0
    start_pose: bool = False
    stop_pose: bool = False

    def numeric(self) -> dict[str, float]:
        return {
            "energy": self.energy,
            "x": self.x,
            "y": self.y,
            "spread": self.spread,
            "brow": self.brow,
            "mouth": self.mouth,
            "smile": self.smile,
        }


@dataclass
class CalibrationProfile:
    name: str
    input_source: str
    neutral: dict[str, float] = field(default_factory=dict)
    minimum: dict[str, float] = field(default_factory=dict)
    maximum: dict[str, float] = field(default_factory=dict)

    def normalize(self, name: str, value: float) -> float:
        low = self.minimum.get(name, 0.0)
        high = self.maximum.get(name, 1.0)
        if high - low < 1e-5:
            return 0.0 if name == "energy" else 0.5
        return clamp((value - low) / (high - low))

    def save(self) -> None:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        path = os.path.join(PROFILE_DIR, f"{self.name}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.__dict__, handle, indent=2)

    @classmethod
    def load(cls, name: str, input_source: str) -> "CalibrationProfile":
        path = os.path.join(PROFILE_DIR, f"{name}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return cls(**json.load(handle))
        return cls(
            name=name,
            input_source=input_source,
            neutral={"energy": 0.0, "x": 0.5, "y": 0.5},
            minimum={"energy": 0.002, "x": 0.2, "y": 0.2, "spread": 0.05, "brow": 0.05, "mouth": 0.02, "smile": 0.0},
            maximum={"energy": 0.04, "x": 0.8, "y": 0.8, "spread": 0.9, "brow": 0.35, "mouth": 0.5, "smile": 1.0},
        )


class CalibrationSession:
    def __init__(self, profile: CalibrationProfile):
        self.profile = profile
        self.phase = "idle"
        self.started_at = 0.0
        self.samples: list[dict[str, float]] = []

    def start(self, now: float) -> None:
        self.phase = "neutral"
        self.started_at = now
        self.samples.clear()

    def update(self, now: float, signals: RawSignals) -> None:
        if self.phase == "idle" or not signals.tracking:
            return
        self.samples.append(signals.numeric())
        elapsed = now - self.started_at
        if self.phase == "neutral" and elapsed >= 3.0:
            keys = self.samples[0].keys()
            self.profile.neutral = {
                key: float(np.mean([sample[key] for sample in self.samples]))
                for key in keys
            }
            self.profile.minimum = {
                key: float(np.percentile([sample[key] for sample in self.samples], 5))
                for key in keys
            }
            self.phase = "range"
            self.started_at = now
            self.samples.clear()
        elif self.phase == "range" and elapsed >= 5.0:
            keys = self.samples[0].keys()
            for key in keys:
                values = [sample[key] for sample in self.samples]
                self.profile.minimum[key] = min(
                    self.profile.minimum.get(key, min(values)),
                    float(np.percentile(values, 5)),
                )
                self.profile.maximum[key] = float(np.percentile(values, 95))
            self.profile.save()
            self.phase = "done"

    def message(self, now: float) -> str:
        if self.phase == "neutral":
            return f"CALIBRATION: REST {max(0, 3 - (now - self.started_at)):.1f}s"
        if self.phase == "range":
            return f"CALIBRATION: COMFORTABLE MOVEMENT {max(0, 5 - (now - self.started_at)):.1f}s"
        if self.phase == "done":
            return "CALIBRATION SAVED"
        return ""


class FeedbackState:
    def __init__(self):
        self.state = "WAITING"
        self.mode = "beginner"
        self.bpm = 120.0
        self.beat = 0
        self.countdown = -1.0
        self.tracking = True
        self._lock = threading.Lock()

    def set(self, name, value):
        with self._lock:
            setattr(self, name, value)

    def snapshot(self):
        with self._lock:
            return {
                "state": self.state,
                "mode": self.mode,
                "bpm": self.bpm,
                "beat": self.beat,
                "countdown": self.countdown,
                "tracking": self.tracking,
            }


def start_feedback_server(feedback: FeedbackState, port: int):
    osc_dispatcher = dispatcher.Dispatcher()
    osc_dispatcher.map("/feedback/state", lambda _a, v: feedback.set("state", str(v)))
    osc_dispatcher.map("/feedback/mode", lambda _a, v: feedback.set("mode", str(v)))
    osc_dispatcher.map("/feedback/bpm", lambda _a, v: feedback.set("bpm", float(v)))
    osc_dispatcher.map("/feedback/beat", lambda _a, v: feedback.set("beat", int(v)))
    osc_dispatcher.map("/feedback/countdown", lambda _a, v: feedback.set("countdown", float(v)))
    osc_dispatcher.map("/feedback/tracking", lambda _a, v: feedback.set("tracking", bool(v)))
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", port), osc_dispatcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class HoldGate:
    def __init__(self, duration: float):
        self.duration = duration
        self.started_at: float | None = None
        self.latched = False

    def update(self, active: bool, now: float) -> bool:
        if not active:
            self.started_at = None
            self.latched = False
            return False
        if self.started_at is None:
            self.started_at = now
        if not self.latched and now - self.started_at >= self.duration:
            self.latched = True
            return True
        return False


def extract_signals(source, hands_result, face_result, pose_result, previous):
    signals = RawSignals()
    hand_points = []
    if hands_result.hand_landmarks:
        for hand in hands_result.hand_landmarks:
            hand_points.append((hand[WRIST].x, hand[WRIST].y))
            signals.start_pose = signals.start_pose or open_palm(hand)
            signals.stop_pose = signals.stop_pose or closed_fist(hand)
        if source in ("auto", "hands"):
            signals.tracking = True
            signals.energy = movement(hand_points, previous.get("hands"))
            signals.x = float(np.mean([point[0] for point in hand_points]))
            signals.y = float(np.mean([point[1] for point in hand_points]))
            signals.spread = (
                math.dist(hand_points[0], hand_points[1])
                if len(hand_points) > 1 else 0.0
            )
            previous["hands"] = hand_points
            return signals

    face = face_result.face_landmarks[0] if face_result.face_landmarks else None
    pose = pose_result.pose_landmarks[0] if pose_result.pose_landmarks else None
    if source in ("auto", "face") and face is not None:
        height = max(1e-6, abs(face[FACE_CHIN].y - face[FACE_TOP].y))
        signals.tracking = True
        signals.brow = clamp((
            face[LEFT_EYE].y - face[LEFT_BROW].y
            + face[RIGHT_EYE].y - face[RIGHT_BROW].y
        ) / (2 * height) * 4)
        signals.mouth = clamp(abs(face[LOWER_LIP].y - face[UPPER_LIP].y) / height * 5)
        signals.smile = clamp((abs(face[RIGHT_MOUTH].x - face[LEFT_MOUTH].x) - 0.25) / 0.2)
        signals.x = face[1].x
        signals.y = face[1].y
        signals.start_pose = signals.brow > 0.65
        signals.stop_pose = signals.mouth > 0.7
    if source in ("auto", "body") and pose is not None:
        points = [(pose[index].x, pose[index].y) for index in POSE_POINTS]
        body_energy = movement(points, previous.get("pose"))
        center_x = np.mean([pose[index].x for index in (11, 12, 23, 24)])
        center_y = np.mean([pose[index].y for index in (11, 12, 23, 24)])
        body_spread = math.hypot(
            pose[LEFT_WRIST].x - pose[RIGHT_WRIST].x,
            pose[LEFT_WRIST].y - pose[RIGHT_WRIST].y,
        )
        previous["pose"] = points
        if source == "body" or not signals.tracking:
            signals.tracking = True
            signals.x = float(center_x)
            signals.y = float(center_y)
        signals.energy = max(signals.energy, body_energy)
        signals.spread = body_spread
        signals.start_pose = signals.start_pose or body_energy > 0.035
    return signals


def draw_text(image, text, position, scale=0.55, color=(235, 235, 235), thickness=1):
    cv2.putText(image, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 3)
    cv2.putText(image, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness)


def draw_bar(panel, label, value, y, color):
    draw_text(panel, label, (24, y), 0.5)
    cv2.rectangle(panel, (24, y + 10), (376, y + 30), (55, 55, 60), -1)
    cv2.rectangle(panel, (24, y + 10), (24 + int(352 * clamp(value)), y + 30), color, -1)


def render_ui(
    frame, mode, source, values, feedback, calibration, style_index, section_index
):
    height = max(620, frame.shape[0])
    camera = cv2.resize(frame, (int(frame.shape[1] * height / frame.shape[0]), height))
    panel = np.full((height, 420, 3), (25, 27, 31), dtype=np.uint8)
    state = feedback["state"]
    state_color = {
        "ACTIVE": (90, 240, 110),
        "ARMED": (0, 210, 255),
        "STOP_QUEUED": (60, 160, 255),
        "HOLD": (255, 210, 80),
        "EMERGENCY_STOP": (60, 60, 255),
    }.get(state, (190, 190, 190))
    draw_text(panel, "CONDUCTOR", (24, 38), 0.9, (255, 255, 255), 2)
    draw_text(panel, f"{mode.value.upper()}  |  INPUT: {source.upper()}", (24, 67), 0.52, (160, 200, 255))
    cv2.rectangle(panel, (20, 84), (400, 142), (40, 43, 49), -1)
    draw_text(panel, state.replace("_", " "), (34, 120), 0.8, state_color, 2)
    if feedback["countdown"] >= 0:
        draw_text(panel, f"IN {math.ceil(feedback['countdown'])} BEATS", (230, 119), 0.55, state_color, 2)
    for beat in range(1, 5):
        color = (80, 255, 120) if beat == feedback["beat"] else (75, 75, 80)
        cv2.circle(panel, (62 + (beat - 1) * 90, 174), 22, color, -1)
        draw_text(panel, str(beat), (55 + (beat - 1) * 90, 182), 0.55, (15, 15, 15), 2)
    draw_text(panel, f"BPM {feedback['bpm']:.1f}", (24, 220), 0.62)
    tracking_color = (80, 240, 110) if values["tracking"] else (70, 70, 255)
    draw_text(panel, "TRACKING OK" if values["tracking"] else "TRACKING LOST - VALUES HELD", (150, 220), 0.48, tracking_color, 2)

    y = 255
    draw_bar(panel, "ENERGY", values["energy"], y, (80, 130, 255))
    y += 58
    if mode in (InteractionMode.ASSISTED, InteractionMode.EXPERT):
        draw_bar(panel, "FOLLOW PERFORMER", values["follow"], y, (100, 230, 180))
        y += 58
        draw_bar(panel, "RHYTHMIC PULSE", values["pulse"], y, (210, 180, 80))
        y += 58
    if mode == InteractionMode.EXPERT:
        draw_bar(panel, "ADVENTURE", values["adventure"], y, (220, 100, 220))
        y += 58
        draw_bar(panel, "STYLE COMMITMENT", values["style_commitment"], y, (180, 220, 90))
        y += 58
        draw_text(panel, f"STYLE: {STYLE_NAMES[style_index]}", (24, y), 0.5, (160, 210, 255))
        draw_text(panel, f"SECTION: {section_index + 1}", (24, y + 28), 0.5, (160, 210, 255))

    calibration_message = calibration.message(time.monotonic())
    if calibration_message:
        cv2.rectangle(panel, (16, height - 104), (404, height - 58), (45, 70, 100), -1)
        draw_text(panel, calibration_message, (26, height - 76), 0.47, (255, 255, 255), 2)
    draw_text(panel, "1/2/3 mode  C calibrate  SPACE hold/resume", (20, height - 34), 0.38, (170, 170, 175))
    draw_text(panel, "S start  X end  E emergency  [/] style  N section", (20, height - 15), 0.38, (170, 170, 175))
    return np.hstack([camera, panel])


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=1)
    parser.add_argument("--mode", choices=[mode.value for mode in InteractionMode], default="beginner")
    parser.add_argument("--input", choices=("auto", "hands", "face", "body"), default="auto")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--control-port", type=int, default=9000)
    parser.add_argument("--feedback-port", type=int, default=9002)
    return parser.parse_args()


def main():
    args = parse_args()
    control = udp_client.SimpleUDPClient("127.0.0.1", args.control_port)
    feedback_state = FeedbackState()
    feedback_server = start_feedback_server(feedback_state, args.feedback_port)
    mode = InteractionMode(args.mode)
    source = args.input
    profile = CalibrationProfile.load(args.profile, source)
    calibration = CalibrationSession(profile)

    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    hand_model = ensure_model("hand_landmarker.task", "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")
    face_model = ensure_model("face_landmarker.task", "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task")
    pose_model = ensure_model("pose_landmarker.task", "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task")
    running = mp_vision.RunningMode.VIDEO
    hands = mp_vision.HandLandmarker.create_from_options(mp_vision.HandLandmarkerOptions(base_options=mp_python.BaseOptions(model_asset_path=hand_model), running_mode=running, num_hands=2))
    face = mp_vision.FaceLandmarker.create_from_options(mp_vision.FaceLandmarkerOptions(base_options=mp_python.BaseOptions(model_asset_path=face_model), running_mode=running, num_faces=1))
    pose = mp_vision.PoseLandmarker.create_from_options(mp_vision.PoseLandmarkerOptions(base_options=mp_python.BaseOptions(model_asset_path=pose_model), running_mode=running))

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open iPhone camera {args.camera}")

    previous = {}
    stop_gate = HoldGate(0.8)
    start_gate = HoldGate(0.35)
    last_send = 0.0
    last_tracking_send = None
    frame_timestamp_ms = 0
    held = False
    style_index = 0
    section_index = 0
    energy_floor = 0.0  # soft minimum from performer intensity
    values = {
        "energy": 0.5,
        "follow": 0.5,
        "pulse": 0.5,
        "adventure": 0.35,
        "style_commitment": 0.4,
        "tracking": False,
    }

    # Listen for performer energy floor on port 9004
    _perf_disp = dispatcher.Dispatcher()
    def _recv_floor(_addr, value):
        nonlocal energy_floor
        energy_floor = float(value)
    _perf_disp.map("/performer/energy_floor", _recv_floor)
    _perf_server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 9004), _perf_disp)
    threading.Thread(target=_perf_server.serve_forever, daemon=True).start()

    control.send_message("/conductor/mode", mode.value)

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
            raw = extract_signals(
                source,
                hands.detect_for_video(image, frame_timestamp_ms),
                face.detect_for_video(image, frame_timestamp_ms),
                pose.detect_for_video(image, frame_timestamp_ms),
                previous,
            )
            calibration.update(now, raw)
            values["tracking"] = raw.tracking

            if raw.tracking and calibration.phase not in ("neutral", "range"):
                energy = profile.normalize("energy", raw.energy)
                x = profile.normalize("x", raw.x)
                y = profile.normalize("y", raw.y)
                spread = profile.normalize("spread", raw.spread)
                brow = profile.normalize("brow", raw.brow)
                if mode == InteractionMode.BEGINNER:
                    values["energy"] = 0.2 if energy < 0.33 else 0.5 if energy < 0.67 else 0.85
                else:
                    values["energy"] = 0.8 * values["energy"] + 0.2 * energy
                    values["follow"] = 0.85 * values["follow"] + 0.15 * x
                    values["pulse"] = 0.85 * values["pulse"] + 0.15 * (1.0 - y)
                if mode == InteractionMode.EXPERT:
                    values["adventure"] = 0.85 * values["adventure"] + 0.15 * spread
                    values["style_commitment"] = 0.85 * values["style_commitment"] + 0.15 * brow

            if start_gate.update(raw.start_pose, now):
                control.send_message("/conductor/action/start", 1)
            if stop_gate.update(raw.stop_pose, now):
                control.send_message("/conductor/action/stop", 1)

            if last_tracking_send != raw.tracking:
                control.send_message("/conductor/tracking", int(raw.tracking))
                last_tracking_send = raw.tracking
            if not held and now - last_send >= 0.08:
                # Apply soft floor from performer intensity — nudge, not lock
                effective_energy = max(values["energy"], energy_floor * 0.85)
                control.send_message("/conductor/energy", effective_energy)
                if mode in (InteractionMode.ASSISTED, InteractionMode.EXPERT):
                    control.send_message("/conductor/follow", values["follow"])
                    control.send_message("/conductor/pulse", values["pulse"])
                if mode == InteractionMode.EXPERT:
                    control.send_message("/conductor/adventure", values["adventure"])
                    control.send_message("/conductor/style_commitment", values["style_commitment"])
                    control.send_message("/conductor/style", style_index)
                    control.send_message("/conductor/section", section_index)
                # Always broadcast style to performer HUD (port 9003)
                udp_client.SimpleUDPClient("127.0.0.1", 9003).send_message(
                    "/conductor/style", style_index
                )
                udp_client.SimpleUDPClient("127.0.0.1", 9003).send_message(
                    "/conductor/energy", effective_energy
                )
                last_send = now

            display = render_ui(
                frame, mode, source, values, feedback_state.snapshot(),
                calibration, style_index, section_index,
            )
            cv2.imshow("Accessible MRT2 Conductor", display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key in (ord("1"), ord("2"), ord("3")):
                mode = list(InteractionMode)[key - ord("1")]
                control.send_message("/conductor/mode", mode.value)
            elif key == ord("c"):
                calibration.start(now)
            elif key == ord("s"):
                control.send_message("/conductor/action/start", 1)
            elif key == ord("x"):
                control.send_message("/conductor/action/stop", 1)
            elif key == ord("e"):
                control.send_message("/conductor/action/emergency_stop", 1)
            elif key == ord(" "):
                held = not held
                control.send_message(
                    "/conductor/action/hold" if held else "/conductor/action/resume",
                    1,
                )
            elif key == ord("["):
                style_index = (style_index - 1) % len(STYLE_NAMES)
            elif key == ord("]"):
                style_index = (style_index + 1) % len(STYLE_NAMES)
            elif key == ord("n"):
                section_index += 1
    finally:
        cap.release()
        cv2.destroyAllWindows()
        hands.close()
        face.close()
        pose.close()
        feedback_server.shutdown()
        feedback_server.server_close()


if __name__ == "__main__":
    main()
