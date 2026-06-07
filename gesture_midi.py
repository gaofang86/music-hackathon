"""
Gesture-controlled MIDI instrument — dual-track edition.

Track A (channel 1): Hand gestures → MIDI notes
  Left hand  → pitch (wrist Y position maps to MIDI notes C3-C6)
  Right hand → trigger (index extended = note ON, fist = note OFF)
               thumb-index distance = velocity

Track B (channel 2): Face + Pose → MIDI CC messages (MRT2 parameters)
  CC 20 — mouth open ratio  → Style slider
  CC 21 — eyebrow raise     → Chaos
  CC 22 — smile (mouth width) → CC 22
  CC 23 — body movement energy → CC 23  (EMA smoothed, alpha=0.2)
  CC 24 — body center X lean   → CC 24
  CC 25 — arms spread          → CC 25
"""

import time
import math
import cv2
import mediapipe as mp
import rtmidi
import numpy as np
import urllib.request
import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIDI_CHANNEL     = 0          # channel 1 (0-indexed in rtmidi)
CC_CHANNEL       = 1          # channel 2 for CC messages (0-indexed)
NOTE_MIN         = 48         # C3
NOTE_MAX         = 84         # C6  (36 semitones)
VELOCITY_MIN     = 30
VELOCITY_MAX     = 127
DEBOUNCE_S       = 0.10       # 100 ms

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B"]

# MediaPipe hand landmark indices
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

# CC numbers
CC_MOUTH   = 20
CC_EYEBROW = 21
CC_SMILE   = 22
CC_ENERGY  = 23
CC_LEAN    = 24
CC_SPREAD  = 25

# CC smoothing factor (low-pass)
CC_SMOOTH = 0.7   # smoothed = 0.7 * prev + 0.3 * new

# EMA alpha for body energy
ENERGY_ALPHA = 0.2


# ---------------------------------------------------------------------------
# Model paths / downloads
# ---------------------------------------------------------------------------
def _ensure_model(filename: str, url: str) -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if not os.path.exists(path):
        print(f"[INFO] Downloading {filename} ...")
        urllib.request.urlretrieve(url, path)
        print(f"[INFO] {filename} downloaded.")
    return path


# ---------------------------------------------------------------------------
# Helpers — hand
# ---------------------------------------------------------------------------
def midi_note_name(midi_note: int) -> str:
    octave = (midi_note // 12) - 1
    name   = NOTE_NAMES[midi_note % 12]
    return f"{name}{octave}"


def y_to_midi(norm_y: float) -> int:
    note = NOTE_MAX - round(norm_y * (NOTE_MAX - NOTE_MIN))
    return int(np.clip(note, NOTE_MIN, NOTE_MAX))


def finger_extended(lm, tip_idx: int, pip_idx: int) -> bool:
    return lm[tip_idx].y < lm[pip_idx].y


def index_extended(lm) -> bool:
    return finger_extended(lm, INDEX_TIP, INDEX_PIP)


def is_fist(lm) -> bool:
    return all(not finger_extended(lm, tip, pip)
               for tip, pip in zip(FINGER_TIPS, FINGER_PIPS))


def thumb_index_distance(lm) -> float:
    dx = lm[THUMB_TIP].x - lm[INDEX_TIP].x
    dy = lm[THUMB_TIP].y - lm[INDEX_TIP].y
    return math.sqrt(dx * dx + dy * dy)


def distance_to_velocity(dist: float) -> int:
    v = int(np.interp(dist, [0.02, 0.35], [VELOCITY_MIN, VELOCITY_MAX]))
    return int(np.clip(v, VELOCITY_MIN, VELOCITY_MAX))


# ---------------------------------------------------------------------------
# Helpers — face landmarks
# Face landmarker indices (subset of 478-point mesh):
#   Upper lip top center  : 13
#   Lower lip bottom center: 14
#   Left mouth corner     : 61
#   Right mouth corner    : 291
#   Forehead (nose bridge): 168
#   Left eye center       : 468 (or approx 386)
#   Right eye center      : 473 (or approx 159)
#   Left eyebrow mid      : 105
#   Right eyebrow mid     : 334
#   Chin                  : 152
# ---------------------------------------------------------------------------
_FL_UPPER_LIP   = 13
_FL_LOWER_LIP   = 14
_FL_L_MOUTH     = 61
_FL_R_MOUTH     = 291
_FL_FOREHEAD    = 10   # top of head proxy
_FL_CHIN        = 152
_FL_L_EYE       = 386  # left eye upper lid
_FL_R_EYE       = 159  # right eye upper lid
_FL_L_BROW      = 105  # left eyebrow mid
_FL_R_BROW      = 334  # right eyebrow mid


def face_metrics(face_lm) -> tuple[float, float, float]:
    """
    Returns (mouth_ratio, eyebrow_ratio, smile_ratio) each in [0, 1].
    face_lm: list of NormalizedLandmark from FaceLandmarker
    """
    # Face height proxy (forehead to chin, Y axis)
    face_height = abs(face_lm[_FL_CHIN].y - face_lm[_FL_FOREHEAD].y)
    if face_height < 1e-6:
        return 0.0, 0.0, 0.0

    # Mouth open: distance between upper and lower lip center
    mouth_open = abs(face_lm[_FL_LOWER_LIP].y - face_lm[_FL_UPPER_LIP].y)
    mouth_ratio = np.clip(mouth_open / face_height * 5.0, 0.0, 1.0)  # scale ×5 for range

    # Eyebrow raise: how far eyebrows are above eye lids (negative Y = higher)
    l_brow_above = face_lm[_FL_L_EYE].y - face_lm[_FL_L_BROW].y
    r_brow_above = face_lm[_FL_R_EYE].y - face_lm[_FL_R_BROW].y
    avg_brow_above = (l_brow_above + r_brow_above) * 0.5
    eyebrow_ratio = np.clip(avg_brow_above / face_height * 4.0, 0.0, 1.0)

    # Smile: mouth width relative to face width
    face_width = abs(face_lm[_FL_R_MOUTH].x - face_lm[_FL_L_MOUTH].x)
    # Neutral mouth width is roughly 0.3–0.35 of face width;
    # a big smile stretches it. Normalise so ~0 = neutral, ~1 = wide smile.
    smile_ratio = np.clip((face_width - 0.25) / 0.20, 0.0, 1.0)

    return float(mouth_ratio), float(eyebrow_ratio), float(smile_ratio)


# ---------------------------------------------------------------------------
# Helpers — pose landmarks
# Pose landmarker indices:
#   11=left shoulder, 12=right shoulder
#   23=left hip,      24=right hip
#   15=left wrist,    16=right wrist
# ---------------------------------------------------------------------------
_PL_L_SHOULDER = 11
_PL_R_SHOULDER = 12
_PL_L_HIP      = 23
_PL_R_HIP      = 24
_PL_L_WRIST    = 15
_PL_R_WRIST    = 16


def pose_metrics(
    pose_lm,
    prev_landmarks: list | None,
    energy_ema: float,
) -> tuple[float, float, float, float, list]:
    """
    Returns (energy_ratio, lean_ratio, spread_ratio, new_energy_ema, landmark_snapshot).
    All ratios in [0, 1].
    """
    # Snapshot: just the 6 key points as (x, y) pairs
    key_ids = [_PL_L_SHOULDER, _PL_R_SHOULDER, _PL_L_HIP, _PL_R_HIP,
               _PL_L_WRIST, _PL_R_WRIST]
    snap = [(pose_lm[i].x, pose_lm[i].y) for i in key_ids]

    # Energy: frame-to-frame velocity of key points
    raw_energy = 0.0
    if prev_landmarks is not None:
        diffs = [math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)
                 for a, b in zip(snap, prev_landmarks)]
        raw_energy = sum(diffs) / len(diffs)

    # EMA smooth (alpha=0.2)
    new_ema = ENERGY_ALPHA * raw_energy + (1 - ENERGY_ALPHA) * energy_ema
    # Typical per-frame motion is 0–0.05 range; scale to [0,1]
    energy_ratio = float(np.clip(new_ema / 0.04, 0.0, 1.0))

    # Body center X (average of shoulders + hips)
    center_x = (
        pose_lm[_PL_L_SHOULDER].x +
        pose_lm[_PL_R_SHOULDER].x +
        pose_lm[_PL_L_HIP].x +
        pose_lm[_PL_R_HIP].x
    ) / 4.0
    lean_ratio = float(np.clip(center_x, 0.0, 1.0))

    # Arms spread: distance between left and right wrists
    dx = pose_lm[_PL_L_WRIST].x - pose_lm[_PL_R_WRIST].x
    dy = pose_lm[_PL_L_WRIST].y - pose_lm[_PL_R_WRIST].y
    spread = math.sqrt(dx*dx + dy*dy)
    # Max natural spread is roughly 1.2 (normalized); map 0–1.0 → 0–1
    spread_ratio = float(np.clip(spread / 1.0, 0.0, 1.0))

    return energy_ratio, lean_ratio, spread_ratio, float(new_ema), snap


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

            is_black = "#" in name
            base_col = (60, 60, 60) if is_black else (90, 90, 90)

            active = (self.active_note == midi)
            col    = (0, int(200 * self.active_vel / 127 + 55), 0) if active else base_col
            cv2.rectangle(panel, (0, y0), (self.w - 1, y1), col, -1)

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

    def control_change(self, cc: int, value: int, channel: int = 1):
        self.midiout.send_message([0xB0 | channel, cc, int(np.clip(value, 0, 127))])

    def close(self):
        self.silence()
        del self.midiout


# ---------------------------------------------------------------------------
# Overlay helpers
# ---------------------------------------------------------------------------
def put_text(img, text: str, pos, scale=0.7, color=(255, 255, 255), thickness=2):
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), thickness + 2)
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, thickness)


def draw_status(frame, note: int | None, velocity: int,
                trigger: bool, pitch_y: float | None):
    h, w = frame.shape[:2]

    cv2.rectangle(frame, (0, 0), (w, 55), (0, 0, 0), -1)
    cv2.rectangle(frame, (0, 0), (w, 55), (50, 50, 50), 1)

    note_str = midi_note_name(note) if note is not None else "---"
    vel_str  = str(velocity)        if note is not None else "---"

    put_text(frame, f"Note: {note_str}",  (10, 22),  color=(100, 255, 100))
    put_text(frame, f"Vel:  {vel_str}",   (10, 45),  color=(100, 200, 255))
    put_text(frame, "ON" if trigger else "OFF",
             (w - 90, 35), scale=0.9,
             color=(0, 255, 80) if trigger else (80, 80, 255))

    if pitch_y is not None:
        bar_y = int(pitch_y * (h - 60)) + 60
        cv2.line(frame, (0, bar_y), (8, bar_y), (0, 255, 128), 3)


def draw_cc_panel(frame, cc_vals: dict[str, float]):
    """
    Draw a small overlay panel (bottom-left) showing face + pose CC values.
    cc_vals keys: mouth, eyebrow, smile, energy, lean, spread  (all 0..1)
    """
    h, w = frame.shape[:2]
    panel_x = 10
    panel_y = h - 170
    bar_max_w = 110
    bar_h = 14
    gap = 22
    bg_pad = 6

    labels = [
        ("Mouth",   "mouth",   (80, 200, 255)),
        ("Brow",    "eyebrow", (150, 255, 100)),
        ("Smile",   "smile",   (255, 180, 80)),
        ("Energy",  "energy",  (255, 80, 80)),
        ("Lean",    "lean",    (200, 100, 255)),
        ("Spread",  "spread",  (80, 255, 220)),
    ]

    # Semi-transparent background
    x0 = panel_x - bg_pad
    y0 = panel_y - bg_pad
    x1 = panel_x + 160 + bg_pad
    y1 = panel_y + len(labels) * gap + bg_pad
    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    cv2.putText(frame, "Track B  CC", (panel_x, panel_y - bg_pad + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1)

    for i, (label, key, color) in enumerate(labels):
        ry = panel_y + i * gap + 14
        val = cc_vals.get(key, 0.0)
        bar_w = int(val * bar_max_w)
        cv2.rectangle(frame, (panel_x + 52, ry - bar_h + 2),
                      (panel_x + 52 + bar_max_w, ry + 2), (50, 50, 50), -1)
        if bar_w > 0:
            cv2.rectangle(frame, (panel_x + 52, ry - bar_h + 2),
                          (panel_x + 52 + bar_w, ry + 2), color, -1)
        cv2.putText(frame, f"{label:<7} {int(val*127):3d}",
                    (panel_x, ry), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (200, 200, 200), 1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # --- MIDI ---
    midi = MidiSender("GestureInstrument")

    # --- MediaPipe imports ---
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    BaseOptions         = mp_python.BaseOptions
    HandLandmarker      = mp_vision.HandLandmarker
    HandLandmarkerOptions = mp_vision.HandLandmarkerOptions
    FaceLandmarker      = mp_vision.FaceLandmarker
    FaceLandmarkerOptions = mp_vision.FaceLandmarkerOptions
    PoseLandmarker      = mp_vision.PoseLandmarker
    PoseLandmarkerOptions = mp_vision.PoseLandmarkerOptions
    VisionRunningMode   = mp_vision.RunningMode

    # --- Download / verify models ---
    hand_model = _ensure_model(
        "hand_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
        "hand_landmarker/float16/1/hand_landmarker.task",
    )
    face_model = _ensure_model(
        "face_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/1/face_landmarker.task",
    )
    pose_model = _ensure_model(
        "pose_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    )

    # --- Create landmarkers ---
    hands = HandLandmarker.create_from_options(HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=hand_model),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    ))

    face_det = FaceLandmarker.create_from_options(FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=face_model),
        running_mode=VisionRunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    ))

    pose_det = PoseLandmarker.create_from_options(PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=pose_model),
        running_mode=VisionRunningMode.VIDEO,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    ))

    # Hand connections (MediaPipe 0.10+ removed mp.solutions)
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

    # --- State: hand / note ---
    roll         = PianoRoll()
    frame_ts_ms : int = 0
    last_note   : int | None = None
    last_trigger: bool       = False
    last_on_time: float      = 0.0
    current_vel : int        = 80
    current_pitch: int | None = None

    # --- State: CC smoothed values (0..127 float) ---
    cc_smooth = {
        CC_MOUTH:   0.0,
        CC_EYEBROW: 0.0,
        CC_SMILE:   0.0,
        CC_ENERGY:  0.0,
        CC_LEAN:    63.5,  # start centred
        CC_SPREAD:  0.0,
    }

    # --- State: pose energy EMA ---
    energy_ema      : float      = 0.0
    prev_pose_snap  : list | None = None

    # Display ratios for CC panel
    cc_display = {k: 0.0 for k in ("mouth", "eyebrow", "smile", "energy", "lean", "spread")}

    print("[INFO] Running — press 'q' to quit.")
    print("[INFO] Track A ch1: Left hand = pitch, Right hand = trigger")
    print("[INFO] Track B ch2: Face + Pose → CC 20-25")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame    = cv2.flip(frame, 1)
        h, w     = frame.shape[:2]
        rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # All three landmarkers share the same timestamp counter
        frame_ts_ms += 33

        # ------------------------------------------------------------------ #
        # Track A — hand detection
        # ------------------------------------------------------------------ #
        res = hands.detect_for_video(mp_image, frame_ts_ms)

        left_lm  = None
        right_lm = None

        if res.hand_landmarks and res.handedness:
            for lm_list, handedness in zip(res.hand_landmarks, res.handedness):
                label = handedness[0].category_name
                if label == "Left":
                    right_lm = lm_list
                else:
                    left_lm  = lm_list

                for lm in lm_list:
                    cx, cy = int(lm.x * w), int(lm.y * h)
                    cv2.circle(frame, (cx, cy), 5, (0, 255, 0), -1)
                for connection in HAND_CONNECTIONS:
                    a, b = connection
                    ax, ay = int(lm_list[a].x * w), int(lm_list[a].y * h)
                    bx, by = int(lm_list[b].x * w), int(lm_list[b].y * h)
                    cv2.line(frame, (ax, ay), (bx, by), (255, 255, 255), 2)

        pitch_lm = left_lm if left_lm is not None else right_lm
        pitch_y_norm: float | None = None
        if pitch_lm is not None:
            pitch_y_norm  = pitch_lm[WRIST].y
            current_pitch = y_to_midi(pitch_y_norm)

        trig_lm   = right_lm if right_lm is not None else left_lm
        triggered = False
        if trig_lm is not None:
            if index_extended(trig_lm):
                triggered   = True
                dist        = thumb_index_distance(trig_lm)
                current_vel = distance_to_velocity(dist)

        now = time.time()
        if triggered and current_pitch is not None:
            if not last_trigger or (current_pitch != last_note):
                midi.note_on(current_pitch, current_vel)
                last_note    = current_pitch
                last_on_time = now
                roll.update(current_pitch, current_vel)
        elif not triggered and last_trigger:
            midi.silence()
            roll.update(None, 0)
            last_note = None

        last_trigger = triggered

        # ------------------------------------------------------------------ #
        # Track B — face detection → CC 20-22
        # ------------------------------------------------------------------ #
        face_res = face_det.detect_for_video(mp_image, frame_ts_ms)
        if face_res.face_landmarks:
            fl = face_res.face_landmarks[0]
            mouth_r, eyebrow_r, smile_r = face_metrics(fl)

            # Smooth and send
            cc_smooth[CC_MOUTH]   = CC_SMOOTH * cc_smooth[CC_MOUTH]   + (1-CC_SMOOTH) * mouth_r   * 127
            cc_smooth[CC_EYEBROW] = CC_SMOOTH * cc_smooth[CC_EYEBROW] + (1-CC_SMOOTH) * eyebrow_r * 127
            cc_smooth[CC_SMILE]   = CC_SMOOTH * cc_smooth[CC_SMILE]   + (1-CC_SMOOTH) * smile_r   * 127

            midi.control_change(CC_MOUTH,   int(cc_smooth[CC_MOUTH]),   CC_CHANNEL)
            midi.control_change(CC_EYEBROW, int(cc_smooth[CC_EYEBROW]), CC_CHANNEL)
            midi.control_change(CC_SMILE,   int(cc_smooth[CC_SMILE]),   CC_CHANNEL)

            cc_display["mouth"]   = cc_smooth[CC_MOUTH]   / 127
            cc_display["eyebrow"] = cc_smooth[CC_EYEBROW] / 127
            cc_display["smile"]   = cc_smooth[CC_SMILE]   / 127

            # Draw face mesh dots (subtle)
            for lm in fl:
                cx, cy = int(lm.x * w), int(lm.y * h)
                cv2.circle(frame, (cx, cy), 1, (255, 200, 80), -1)

        # ------------------------------------------------------------------ #
        # Track B — pose detection → CC 23-25
        # ------------------------------------------------------------------ #
        pose_res = pose_det.detect_for_video(mp_image, frame_ts_ms)
        if pose_res.pose_landmarks:
            pl = pose_res.pose_landmarks[0]

            energy_r, lean_r, spread_r, energy_ema, prev_pose_snap = pose_metrics(
                pl, prev_pose_snap, energy_ema
            )

            cc_smooth[CC_ENERGY] = CC_SMOOTH * cc_smooth[CC_ENERGY] + (1-CC_SMOOTH) * energy_r * 127
            cc_smooth[CC_LEAN]   = CC_SMOOTH * cc_smooth[CC_LEAN]   + (1-CC_SMOOTH) * lean_r   * 127
            cc_smooth[CC_SPREAD] = CC_SMOOTH * cc_smooth[CC_SPREAD] + (1-CC_SMOOTH) * spread_r * 127

            midi.control_change(CC_ENERGY, int(cc_smooth[CC_ENERGY]), CC_CHANNEL)
            midi.control_change(CC_LEAN,   int(cc_smooth[CC_LEAN]),   CC_CHANNEL)
            midi.control_change(CC_SPREAD, int(cc_smooth[CC_SPREAD]), CC_CHANNEL)

            cc_display["energy"] = cc_smooth[CC_ENERGY] / 127
            cc_display["lean"]   = cc_smooth[CC_LEAN]   / 127
            cc_display["spread"] = cc_smooth[CC_SPREAD] / 127

            # Draw pose skeleton (key joints)
            pose_connections = [
                (_PL_L_SHOULDER, _PL_R_SHOULDER),
                (_PL_L_SHOULDER, _PL_L_HIP),
                (_PL_R_SHOULDER, _PL_R_HIP),
                (_PL_L_HIP,      _PL_R_HIP),
                (_PL_L_SHOULDER, _PL_L_WRIST),
                (_PL_R_SHOULDER, _PL_R_WRIST),
            ]
            key_ids = [_PL_L_SHOULDER, _PL_R_SHOULDER, _PL_L_HIP,
                       _PL_R_HIP, _PL_L_WRIST, _PL_R_WRIST]
            for i in key_ids:
                if pl[i].visibility > 0.4:
                    cx, cy = int(pl[i].x * w), int(pl[i].y * h)
                    cv2.circle(frame, (cx, cy), 7, (100, 80, 255), -1)
            for a, b in pose_connections:
                if pl[a].visibility > 0.4 and pl[b].visibility > 0.4:
                    ax, ay = int(pl[a].x * w), int(pl[a].y * h)
                    bx, by = int(pl[b].x * w), int(pl[b].y * h)
                    cv2.line(frame, (ax, ay), (bx, by), (180, 100, 255), 2)

        # ------------------------------------------------------------------ #
        # Build display
        # ------------------------------------------------------------------ #
        draw_status(frame, last_note, current_vel, triggered, pitch_y_norm)
        draw_cc_panel(frame, cc_display)

        roll_img     = roll.draw()
        roll_resized = cv2.resize(roll_img, (ROLL_W, h))
        display      = np.hstack([frame, roll_resized])

        cv2.imshow("Gesture MIDI Instrument", display)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # --- Cleanup ---
    midi.close()
    cap.release()
    cv2.destroyAllWindows()
    hands.close()      # type: ignore
    face_det.close()   # type: ignore
    pose_det.close()   # type: ignore
    print("[INFO] Bye.")


if __name__ == "__main__":
    main()
