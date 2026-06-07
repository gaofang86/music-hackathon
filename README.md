# Gesture MIDI Instrument

Play music with your hands — no instrument required. A webcam captures your hand gestures and converts them into MIDI signals that drive MRT2 Jam in real time.

---

## Requirements

- macOS (tested on macOS 14+)
- Python 3.9+
- Webcam (built-in or external)
- MRT2 Jam (App Store, bundle ID: `com.google.mrt2.jam`)

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Quick Start

**Step 1 — Run the gesture script first:**
```bash
source .venv/bin/activate
python gesture_midi.py
```

**Step 2 — Connect MRT2 Jam:**
Open MRT2 Jam → click **MIDI INPUT** at the bottom → select **GestureInstrument**.

**Step 3 — Play.**

---

## Gestures

| Hand | Gesture | Effect |
|------|---------|--------|
| **Left hand** | Wrist moves up/down | Controls **pitch** (higher = higher note) |
| **Right hand** | Index finger extended | **Note ON** |
| **Right hand** | Fist | **Note OFF** |
| **Right hand** | Thumb–index distance | **Velocity** (wider = louder) |

> If only one hand is visible, it controls both pitch and trigger.

### Range
- Screen bottom → C3 (MIDI 48)
- Screen top → C6 (MIDI 84)
- 3 octaves, 36 semitones

---

## Interface

```
┌──────────────────────────────┬──────────┐
│  Note: G4   Vel: 95    [ON]  │  Piano   │
│                              │  Roll    │
│      [ Camera feed ]         │  C6–C3   │
└──────────────────────────────┴──────────┘
```

Press **`q`** to quit.

---

## How It Works

1. **MediaPipe Hands** detects 21 hand landmarks per frame
2. **Pitch** — left wrist Y position mapped linearly to MIDI notes 48–84
3. **Trigger** — index fingertip Y vs PIP joint Y: tip higher = finger extended = Note ON
4. **Velocity** — Euclidean distance between thumb tip and index tip, mapped to 30–127
5. **Debounce** — same note won't retrigger within 100 ms
6. **MIDI output** — `python-rtmidi` opens a virtual port named `GestureInstrument`; MRT2 reads it directly, no IAC Driver needed

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Camera won't open | System Settings → Privacy → Camera → allow Terminal/Python, then restart Terminal |
| MRT2 can't see MIDI port | Start `gesture_midi.py` first, then open MRT2 |
| Gestures feel unreliable | Use good lighting, keep hand 30–60 cm from camera |
| Note stuck on | Make a fist to send Note OFF, or press `q` to restart |

---

## Files

```
music-hackathon/
├── gesture_midi.py   # main app
├── requirements.txt  # dependencies
├── setup_midi.sh     # IAC Driver setup helper (fallback)
└── README.md
```
