"""
Musician track — head-nod BPM detector + MIDI clock output.

Detects head nods via webcam using MediaPipe Face Landmarker.
Calculates BPM from nod intervals, then drives a MIDI clock (0xF8)
on a virtual port so MRT2 and other MIDI software stay in sync.

Keys:
  r  = reset BPM history
  s  = stop / start clock
  q  = quit
"""

import os
import time
import threading
import collections
import numpy as np
import cv2
import mediapipe as mp
import rtmidi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NOSE_TIP_IDX       = 1       # MediaPipe face mesh landmark index for nose tip

Y_BUFFER_SIZE      = 10      # rolling window for Y positions
NOD_MIN_MAGNITUDE  = 0.015   # normalized coords minimum downward excursion
NOD_MIN_INTERVAL_S = 0.250   # 250 ms → max 240 BPM

BPM_HISTORY        = 4       # exactly 4 nods to establish tempo (one bar of 4/4)
BPM_MIN            = 40.0
BPM_MAX            = 240.0
BPM_DEFAULT        = 120.0
BPM_TIMEOUT_S      = 4.0     # after this, enter WAITING state

MIDI_CLOCKS_PER_BEAT = 24    # standard MIDI spec

NOD_FLASH_MS       = 200     # how long green nod indicator stays lit (ms)


# ---------------------------------------------------------------------------
# NodDetector
# ---------------------------------------------------------------------------
class NodDetector:
    """
    Watches a rolling buffer of nose-tip Y positions (normalized, 0=top).
    A nod is: Y goes DOWN (increases) fast, then comes back up.
    We detect it when the velocity crosses zero from negative to positive
    after a downward spike of at least NOD_MIN_MAGNITUDE.
    """

    def __init__(self):
        self._buf: collections.deque[float] = collections.deque(maxlen=Y_BUFFER_SIZE)
        self._peak_y: float | None = None   # highest Y (lowest on screen) seen in current downswing
        self._peak_start_y: float | None = None   # Y at start of downswing

    def update(self, y: float) -> tuple[bool, float]:
        """
        Push a new Y value. Returns (nod_detected, magnitude).
        magnitude is > 0 only when a nod fires.
        """
        self._buf.append(y)

        if len(self._buf) < 3:
            return False, 0.0

        buf = list(self._buf)
        # Velocity between last two frames
        v_last = buf[-1] - buf[-2]       # positive = moving down
        v_prev = buf[-2] - buf[-3]       # positive = was moving down

        # Track peak during downswing
        if v_prev > 0:
            # Was going down; track how far we got
            if self._peak_start_y is None:
                self._peak_start_y = buf[-3]
            if self._peak_y is None or buf[-2] > self._peak_y:
                self._peak_y = buf[-2]

        # Velocity crossing zero: was going down, now going up → bottom of nod
        if v_prev > 0 and v_last <= 0 and self._peak_y is not None and self._peak_start_y is not None:
            magnitude = self._peak_y - self._peak_start_y
            self._peak_y = None
            self._peak_start_y = None
            if magnitude >= NOD_MIN_MAGNITUDE:
                return True, float(magnitude)

        # Reset peak tracking if velocity has been upward for a while
        if v_last < 0 and v_prev < 0:
            self._peak_y = None
            self._peak_start_y = None

        return False, 0.0


# ---------------------------------------------------------------------------
# BpmTracker
# ---------------------------------------------------------------------------
class BpmTracker:
    """
    Accumulates nod timestamps, calculates BPM from intervals.
    """

    def __init__(self):
        self._timestamps: collections.deque[float] = collections.deque(maxlen=BPM_HISTORY + 1)
        self._bpm: float = BPM_DEFAULT
        self._last_nod_time: float | None = None

    def record_nod(self, t: float | None = None) -> float:
        """Register a nod at time t (defaults to now). Returns new BPM."""
        if t is None:
            t = time.monotonic()

        # Enforce minimum interval
        if self._last_nod_time is not None and (t - self._last_nod_time) < NOD_MIN_INTERVAL_S:
            return self._bpm

        self._last_nod_time = t
        self._timestamps.append(t)

        if len(self._timestamps) >= 2:
            ts = list(self._timestamps)
            intervals = [ts[i+1] - ts[i] for i in range(len(ts) - 1)]
            raw_bpm = 60.0 / np.mean(intervals)
            self._bpm = float(np.clip(raw_bpm, BPM_MIN, BPM_MAX))

        return self._bpm

    def current_bpm(self) -> float:
        return self._bpm

    def is_waiting(self) -> bool:
        """True if no nod received in BPM_TIMEOUT_S seconds."""
        if self._last_nod_time is None:
            return True
        return (time.monotonic() - self._last_nod_time) > BPM_TIMEOUT_S

    def reset(self):
        self._timestamps.clear()
        self._bpm = BPM_DEFAULT
        self._last_nod_time = None


# ---------------------------------------------------------------------------
# MidiClock
# ---------------------------------------------------------------------------
class MidiClock:
    """
    Background thread that fires 24 MIDI timing clock messages (0xF8) per beat.
    BPM can be updated live; the interval adjusts immediately.
    """

    def __init__(self, port_name: str = "MusicianClock"):
        self._midiout = rtmidi.MidiOut()
        self._midiout.open_virtual_port(port_name)
        print(f"[MIDI] Virtual port '{port_name}' opened.")

        self._bpm: float = BPM_DEFAULT
        self._running: bool = False
        self._active: bool = True   # False = thread should exit
        self._started: bool = False  # have we sent 0xFA yet

        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # --- public API ---------------------------------------------------------

    def start(self):
        """Send MIDI Start (0xFA) and begin clocking."""
        with self._lock:
            if not self._started:
                self._midiout.send_message([0xFA])
                self._started = True
            self._running = True

    def stop(self):
        """Send MIDI Stop (0xFC) and pause clocking."""
        with self._lock:
            self._midiout.send_message([0xFC])
            self._running = False

    def toggle(self):
        with self._lock:
            running = self._running
        if running:
            self.stop()
        else:
            self.start()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def update_bpm(self, bpm: float):
        with self._lock:
            self._bpm = float(np.clip(bpm, BPM_MIN, BPM_MAX))

    def close(self):
        with self._lock:
            self._active = False
            self._running = False
        self._thread.join(timeout=2.0)
        del self._midiout

    # --- internal -----------------------------------------------------------

    def _loop(self):
        while True:
            with self._lock:
                active = self._active
                running = self._running
                bpm = self._bpm

            if not active:
                break

            if running:
                interval = 60.0 / (bpm * MIDI_CLOCKS_PER_BEAT)
                self._midiout.send_message([0xF8])
                time.sleep(interval)
            else:
                time.sleep(0.005)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def put_text_shadow(img, text: str, pos, scale=1.0,
                    color=(255, 255, 255), thickness=2):
    """Draw text with a dark shadow for readability over camera feed."""
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), thickness + 3)
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, thickness)


def draw_hud(frame, bpm: float, nod_flash: bool, clock_running: bool,
             waiting: bool, nod_history: list[float], clock_started: bool = False):
    """Overlay BPM, nod indicator, clock state, nod history dots."""
    h, w = frame.shape[:2]

    # Semi-transparent black bar at bottom
    bar_h = 160
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    # BPM — big number
    bpm_color = (0, 255, 80) if clock_running and not waiting else (0, 200, 255)
    bpm_str = f"{bpm:.0f}"
    put_text_shadow(frame, f"BPM:  {bpm_str}", (20, h - bar_h + 50),
                    scale=3.0, color=bpm_color, thickness=3)

    # Nod indicator
    nod_x, nod_y = 20, h - bar_h + 90
    if nod_flash:
        cv2.circle(frame, (nod_x + 10, nod_y - 8), 12, (0, 255, 80), -1)
        put_text_shadow(frame, "NOD DETECTED", (nod_x + 30, nod_y),
                        scale=0.8, color=(0, 255, 80), thickness=2)
    else:
        cv2.circle(frame, (nod_x + 10, nod_y - 8), 12, (80, 80, 80), 2)
        put_text_shadow(frame, "waiting for nod...", (nod_x + 30, nod_y),
                        scale=0.7, color=(140, 140, 140), thickness=1)

    # Clock state
    if waiting:
        clock_label = "CLOCK: WAITING"
        clock_color = (0, 200, 255)
    elif clock_running:
        clock_label = "CLOCK: RUNNING"
        clock_color = (0, 255, 80)
    else:
        clock_label = "CLOCK: STOPPED"
        clock_color = (80, 80, 255)

    clk_x = 20
    clk_y = h - bar_h + 120
    cv2.circle(frame, (clk_x + 8, clk_y - 7), 8,
               clock_color if clock_running else (80, 80, 80), -1)
    put_text_shadow(frame, clock_label, (clk_x + 26, clk_y),
                    scale=0.75, color=clock_color, thickness=2)

    # Nod history dots — 4 circles, fill as nods come in
    dot_label_x = 20
    dot_label_y = h - bar_h + 148
    nods_so_far = len(nod_history)
    if not clock_started:
        remaining = BPM_HISTORY - nods_so_far
        label = f"Nod {nods_so_far}/4" if nods_so_far > 0 else "Nod 4x to start"
        label_color = (0, 200, 255)
    else:
        label = "1  2  3  4"
        label_color = (0, 255, 80)
    put_text_shadow(frame, label, (dot_label_x, dot_label_y),
                    scale=0.65, color=label_color, thickness=1)
    for i in range(BPM_HISTORY):
        cx = dot_label_x + 160 + i * 30
        cy = dot_label_y - 7
        if i < nods_so_far:
            cv2.circle(frame, (cx, cy), 9, (0, 200, 60), -1)
        else:
            cv2.circle(frame, (cx, cy), 9, (60, 60, 60), 2)

    # Key hints (top-right corner)
    hints = ["r=reset  s=stop/start  q=quit"]
    put_text_shadow(frame, hints[0], (w - 380, 25),
                    scale=0.5, color=(160, 160, 160), thickness=1)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    # --- MediaPipe setup ---
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    BaseOptions          = mp_python.BaseOptions
    FaceLandmarker       = mp_vision.FaceLandmarker
    FaceLandmarkerOptions = mp_vision.FaceLandmarkerOptions
    VisionRunningMode    = mp_vision.RunningMode

    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "face_landmarker.task")
    if not os.path.exists(model_path):
        import urllib.request
        print("[INFO] Downloading face_landmarker.task ...")
        urllib.request.urlretrieve(
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task",
            model_path,
        )
        print("[INFO] Download complete.")

    face_det = FaceLandmarker.create_from_options(FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=VisionRunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    ))

    # --- MIDI clock ---
    clock = MidiClock("MusicianClock")

    # --- Detectors/trackers ---
    nod_detector = NodDetector()
    bpm_tracker  = BpmTracker()

    # --- State ---
    nod_timestamps: collections.deque[float] = collections.deque(maxlen=BPM_HISTORY)
    nod_flash_until: float = 0.0       # monotonic time until flash expires
    clock_started: bool = False
    frame_ts_ms: int = 0

    # --- Webcam ---
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] Cannot open webcam.")
        clock.close()
        return

    print("[INFO] Musician BPM clock running.")
    print("[INFO] Nod your head to the beat.  r=reset  s=stop/start  q=quit")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame = cv2.flip(frame, 1)
        h, w  = frame.shape[:2]
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        frame_ts_ms += 33

        # --- Face detection ---
        face_res = face_det.detect_for_video(mp_image, frame_ts_ms)

        nod_this_frame = False

        if face_res.face_landmarks:
            fl = face_res.face_landmarks[0]
            nose_y = fl[NOSE_TIP_IDX].y

            # Draw face mesh dots (subtle amber)
            for lm in fl:
                cx, cy = int(lm.x * w), int(lm.y * h)
                cv2.circle(frame, (cx, cy), 1, (255, 200, 80), -1)
            # Highlight nose tip
            nx = int(fl[NOSE_TIP_IDX].x * w)
            ny = int(fl[NOSE_TIP_IDX].y * h)
            cv2.circle(frame, (nx, ny), 6, (0, 200, 255), -1)

            nod_detected, magnitude = nod_detector.update(nose_y)

            if nod_detected:
                now = time.monotonic()
                bpm = bpm_tracker.record_nod(now)
                clock.update_bpm(bpm)
                nod_timestamps.append(now)
                nod_flash_until = now + NOD_FLASH_MS / 1000.0
                nod_this_frame = True

                # Start clock only after 4th nod — one full bar of 4/4
                if not clock_started and len(nod_timestamps) >= BPM_HISTORY:
                    clock.start()
                    clock_started = True
                    print(f"[INFO] Clock started at {bpm:.1f} BPM")

        # --- Flash state ---
        now_mono = time.monotonic()
        nod_flash = now_mono < nod_flash_until

        # --- Waiting state ---
        waiting = bpm_tracker.is_waiting()
        current_bpm = bpm_tracker.current_bpm()

        # --- HUD ---
        draw_hud(
            frame,
            bpm=current_bpm,
            nod_flash=nod_flash,
            clock_running=clock.is_running,
            waiting=waiting,
            nod_history=list(nod_timestamps),
            clock_started=clock_started,
        )

        cv2.imshow("Musician BPM Clock", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('r'):
            bpm_tracker.reset()
            nod_timestamps.clear()
            clock.update_bpm(BPM_DEFAULT)
            print(f"[INFO] BPM history reset to {BPM_DEFAULT}.")
        elif key == ord('s'):
            clock.toggle()
            state = "RUNNING" if clock.is_running else "STOPPED"
            print(f"[INFO] Clock {state}.")

    # --- Cleanup ---
    clock.stop()
    clock.close()
    cap.release()
    cv2.destroyAllWindows()
    face_det.close()  # type: ignore
    print("[INFO] Bye.")


if __name__ == "__main__":
    main()
