"""
Gesture-controlled MIDI instrument.

Left hand  → pitch (wrist Y position maps to MIDI notes C3-C6)
Right hand → trigger (index extended = note ON, fist = note OFF)
             thumb-index distance = velocity
"""

import time
import math
import cv2
import mediapipe as mp
import rtmidi
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIDI_CHANNEL     = 0          # channel 1 (0-indexed in rtmidi)
NOTE_MIN         = 48         # C3
NOTE_MAX         = 84         # C6  (36 semitones)
VELOCITY_MIN     = 30
VELOCITY_MAX     = 127
DEBOUNCE_S       = 0.10       # 100 ms

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B"]

# MediaPipe landmark indices
WRIST           = 0
THUMB_TIP       = 4
INDEX_TIP       = 8;  INDEX_PIP = 6
MIDDLE_TIP      = 12; MIDDLE_PIP = 10
RING_TIP        = 16; RING_PIP   = 14
PINKY_TIP       = 20; PINKY_PIP  = 18

FINGER_TIPS = [INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP]
FINGER_PIPS = [INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP]

# Piano-roll panel dimensions
ROLL_W = 200
ROLL_H = 360  # covers 36 semitones × 10 px each


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def midi_note_name(midi_note: int) -> str:
    octave = (midi_note // 12) - 1
    name   = NOTE_NAMES[midi_note % 12]
    return f"{name}{octave}"


def y_to_midi(norm_y: float) -> int:
    """Normalized Y (0=top, 1=bottom) → MIDI note (top=high, bottom=low)."""
    note = NOTE_MAX - round(norm_y * (NOTE_MAX - NOTE_MIN))
    return int(np.clip(note, NOTE_MIN, NOTE_MAX))


def finger_extended(lm, tip_idx: int, pip_idx: int) -> bool:
    """True when finger tip is above its PIP joint (smaller Y = higher on screen)."""
    return lm[tip_idx].y < lm[pip_idx].y


def index_extended(lm) -> bool:
    return finger_extended(lm, INDEX_TIP, INDEX_PIP)


def is_fist(lm) -> bool:
    """True when all four fingers are curled."""
    return all(not finger_extended(lm, tip, pip)
               for tip, pip in zip(FINGER_TIPS, FINGER_PIPS))


def thumb_index_distance(lm) -> float:
    """Euclidean distance between thumb tip and index tip (normalized coords)."""
    dx = lm[THUMB_TIP].x - lm[INDEX_TIP].x
    dy = lm[THUMB_TIP].y - lm[INDEX_TIP].y
    return math.sqrt(dx * dx + dy * dy)


def distance_to_velocity(dist: float) -> int:
    """Map thumb-index distance (0–0.4 range) to velocity."""
    v = int(np.interp(dist, [0.02, 0.35], [VELOCITY_MIN, VELOCITY_MAX]))
    return int(np.clip(v, VELOCITY_MIN, VELOCITY_MAX))


# ---------------------------------------------------------------------------
# Piano-roll visualizer
# ---------------------------------------------------------------------------
class PianoRoll:
    def __init__(self, width: int = ROLL_W, height: int = ROLL_H):
        self.w = width
        self.h = height
        self.semitones = NOTE_MAX - NOTE_MIN + 1   # 37
        self.row_h = self.h // self.semitones
        self.active_note: int | None = None
        self.active_vel:  int        = 0

    def draw(self) -> np.ndarray:
        panel = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        panel[:] = (30, 30, 30)

        for i in range(self.semitones):
            midi = NOTE_MAX - i
            y0   = i * self.row_h
            y1   = y0 + self.row_h - 1
            name = NOTE_NAMES[midi % 12]

            # White/black key colouring
            is_black = "#" in name
            base_col = (60, 60, 60) if is_black else (90, 90, 90)

            active = (self.active_note == midi)
            col    = (0, int(200 * self.active_vel / 127 + 55), 0) if active else base_col
            cv2.rectangle(panel, (0, y0), (self.w - 1, y1), col, -1)

            # Label every C
            if name == "C":
                octave = (midi // 12) - 1
                cv2.putText(panel, f"C{octave}", (4, y0 + self.row_h - 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)

        return panel

    def update(self, note: int | None, velocity: int = 0):
        self.active_note = note
        self.active_vel  = velocity


# ---------------------------------------------------------------------------
# MIDI output
# ---------------------------------------------------------------------------
class MidiSender:
    def __init__(self, port_name: str = "GestureInstrument"):
        self.midiout = rtmidi.MidiOut()
        self.midiout.open_virtual_port(port_name)
        self._current_note: int | None = None
        print(f"[MIDI] Virtual port '{port_name}' opened.")

    def note_on(self, note: int, velocity: int, channel: int = MIDI_CHANNEL):
        if self._current_note is not None and self._current_note != note:
            self.note_off(self._current_note, channel)
        self.midiout.send_message([0x90 | channel, note, velocity])
        self._current_note = note

    def note_off(self, note: int, channel: int = MIDI_CHANNEL):
        self.midiout.send_message([0x80 | channel, note, 0])
        if self._current_note == note:
            self._current_note = None

    def silence(self, channel: int = MIDI_CHANNEL):
        if self._current_note is not None:
            self.note_off(self._current_note, channel)

    def close(self):
        self.silence()
        del self.midiout


# ---------------------------------------------------------------------------
# Overlay helpers
# ---------------------------------------------------------------------------
def put_text(img, text: str, pos, scale=0.7, color=(255, 255, 255), thickness=2):
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), thickness + 2)   # shadow
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, thickness)


def draw_status(frame, note: int | None, velocity: int,
                trigger: bool, pitch_y: float | None):
    h, w = frame.shape[:2]

    # Background bar
    cv2.rectangle(frame, (0, 0), (w, 55), (0, 0, 0), -1)
    cv2.rectangle(frame, (0, 0), (w, 55), (50, 50, 50), 1)

    note_str = midi_note_name(note) if note is not None else "---"
    vel_str  = str(velocity)        if note is not None else "---"

    put_text(frame, f"Note: {note_str}",     (10, 22),  color=(100, 255, 100))
    put_text(frame, f"Vel:  {vel_str}",      (10, 45),  color=(100, 200, 255))
    put_text(frame, "ON" if trigger else "OFF",
             (w - 90, 35), scale=0.9,
             color=(0, 255, 80) if trigger else (80, 80, 255))

    # Pitch bar on left edge
    if pitch_y is not None:
        bar_y = int(pitch_y * (h - 60)) + 60
        cv2.line(frame, (0, bar_y), (8, bar_y), (0, 255, 128), 3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # --- MIDI ---
    midi = MidiSender("GestureInstrument")

    # --- MediaPipe ---
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    BaseOptions = mp_python.BaseOptions
    HandLandmarker = mp_vision.HandLandmarker
    HandLandmarkerOptions = mp_vision.HandLandmarkerOptions
    VisionRunningMode = mp_vision.RunningMode

    import urllib.request, os, tempfile
    model_path = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")
    if not os.path.exists(model_path):
        print("[INFO] Downloading hand_landmarker.task model (~8MB)...")
        urllib.request.urlretrieve(
            "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
            model_path,
        )
        print("[INFO] Model downloaded.")

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    hands = HandLandmarker.create_from_options(options)

    # MediaPipe 0.10+ removed mp.solutions; define connections manually
    HAND_CONNECTIONS = [
        (0,1),(1,2),(2,3),(3,4),
        (0,5),(5,6),(6,7),(7,8),
        (5,9),(9,10),(10,11),(11,12),
        (9,13),(13,14),(14,15),(15,16),
        (13,17),(17,18),(18,19),(19,20),
        (0,17),
    ]

    # --- Webcam ---
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] Cannot open webcam.")
        return

    # --- State ---
    roll        = PianoRoll()
    frame_ts_ms : int = 0
    last_note   : int | None = None
    last_trigger: bool       = False
    last_on_time: float      = 0.0
    current_vel : int        = 80
    current_pitch: int | None = None

    print("[INFO] Running — press 'q' to quit.")
    print("[INFO] Left hand = pitch, Right hand = trigger (index=ON, fist=OFF)")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame = cv2.flip(frame, 1)   # mirror
        h, w  = frame.shape[:2]
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        frame_ts_ms += 33
        res = hands.detect_for_video(mp_image, frame_ts_ms)

        left_lm  = None   # pitch hand
        right_lm = None   # trigger hand

        if res.hand_landmarks and res.handedness:
            for lm_list, handedness in zip(res.hand_landmarks, res.handedness):
                label = handedness[0].category_name  # "Left" or "Right"
                # After horizontal flip "Left" in mediapipe = user's right
                if label == "Left":
                    right_lm = lm_list
                else:
                    left_lm  = lm_list

                # Draw landmarks manually
                for lm in lm_list:
                    cx, cy = int(lm.x * w), int(lm.y * h)
                    cv2.circle(frame, (cx, cy), 5, (0, 255, 0), -1)
                for connection in HAND_CONNECTIONS:
                    a, b = connection
                    ax, ay = int(lm_list[a].x * w), int(lm_list[a].y * h)
                    bx, by = int(lm_list[b].x * w), int(lm_list[b].y * h)
                    cv2.line(frame, (ax, ay), (bx, by), (255, 255, 255), 2)

        # --- Determine pitch (from left hand, fallback to right) ---
        pitch_lm = left_lm if left_lm is not None else right_lm
        pitch_y_norm: float | None = None
        if pitch_lm is not None:
            pitch_y_norm   = pitch_lm[WRIST].y
            current_pitch  = y_to_midi(pitch_y_norm)

        # --- Determine trigger (right hand, fallback to left) ---
        trig_lm  = right_lm if right_lm is not None else left_lm
        triggered = False
        if trig_lm is not None:
            if index_extended(trig_lm):
                triggered = True
                dist          = thumb_index_distance(trig_lm)
                current_vel   = distance_to_velocity(dist)

        # --- MIDI logic ---
        now = time.time()
        if triggered and current_pitch is not None:
            same_note    = (current_pitch == last_note)
            held_too_long = (now - last_on_time) > DEBOUNCE_S

            if not last_trigger or not same_note:
                # New gesture or pitch changed → send note
                midi.note_on(current_pitch, current_vel)
                last_note    = current_pitch
                last_on_time = now
                roll.update(current_pitch, current_vel)

        elif not triggered and last_trigger:
            # Released → note off
            midi.silence()
            roll.update(None, 0)
            last_note = None

        last_trigger = triggered

        # --- Build display ---
        draw_status(frame, last_note, current_vel, triggered, pitch_y_norm)

        # Merge webcam + piano roll
        roll_img = roll.draw()
        # Resize roll to match frame height (minus status bar)
        roll_resized = cv2.resize(roll_img, (ROLL_W, h))
        display = np.hstack([frame, roll_resized])

        cv2.imshow("Gesture MIDI Instrument", display)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # --- Cleanup ---
    midi.close()
    cap.release()
    cv2.destroyAllWindows()
    hands.close()  # type: ignore
    print("[INFO] Bye.")


if __name__ == "__main__":
    main()
