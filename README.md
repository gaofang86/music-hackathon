# 🎵 Accessible MRT2 Ensemble

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![MediaPipe](https://img.shields.io/badge/MediaPipe-0.10-green)
![MRT2](https://img.shields.io/badge/Magenta-RT2-orange)
![Reaper](https://img.shields.io/badge/Reaper-AU_Plugin-purple)
![OSC](https://img.shields.io/badge/OSC-local-lightgrey)

→ [Design Doc](docs/CONDUCTOR_DESIGN.md) · [Test Guide](docs/TEST_GUIDE.md) · [GitHub](https://github.com/gaofang86/music-hackathon)

A two-person live performance system where a musician and a non-musician co-create music in real time — the musician plays, the conductor shapes.

> *Most AI music tools require musical knowledge to operate. This one doesn't — a dancer, a child, or anyone can conduct the emotional direction of a live performance using only their body.*

---

## The Problem

AI music generation tools are powerful but inaccessible. Operating parameters like `temperature`, `cfg_notes`, and `style_commitment` are meaningless to non-musicians. The result: only technically trained people can direct AI-generated music.

More importantly, live performance needs **two-way interaction**. A musician playing alone with an AI backdrop is a solo act with background music — not a collaboration.

The gap between what a musician can express and what a non-musician can contribute is filled by **the body, not the interface.**

---

## How It Works

| Layer | Who | What it does |
|-------|-----|-------------|
| **Tempo** | Musician | Head nods ×5 establish BPM → MIDI clock → Reaper sync |
| **Content** | Musician | MIDI keyboard → note conditioning → MRT2 |
| **Direction** | Conductor | Body / face / gesture → MusicalIntent → MRT2 parameters |
| **Interaction** | Both | Playing intensity nudges conductor's energy floor; conductor's style changes musician's HUD tint |
| **Output** | — | Reaper audio + MRT2 AU audio → speakers |

---

## Architecture

```
MUSICIAN (laptop cam)                 CONDUCTOR (iPhone cam)
        │                                     │
   Head nods ×5                       Hands / face / body
   BPM established                    MusicalIntent
        │                             energy / style / pulse
        ├── MIDI Clock → Reaper              │
        │   kick / snare / hihat             ▼
        │                             OSC → 127.0.0.1:9000
        ├── MIDI notes → MRT2 AU             │
        │   (melodic conditioning)    EnsembleController
        │                             WAITING→READY→ARMED→ACTIVE
        └──────── bidirectional nudge ───────┘
             playing intensity → conductor energy floor
             conductor style   → performer HUD tint

        Reaper audio + MRT2 AU audio → speakers
```

---

## Interaction

**Energy Push** — Conductor raises energy → MRT2 generates denser music → Musician hears background swelling → plays more intensely

**Performance Feedback** — Musician plays hard (high velocity) → energy floor rises → conductor feels resistance below 0.4 → responds by adjusting style

**Visual Feedback** — Conductor switches style preset → musician's screen tint changes (Warm Acoustic = orange, Dark Cinematic = deep blue, Bright Electronic = cyan)

---

## Setup

```bash
cd ~/Desktop/music-hackathon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Cameras** — Connect iPhone via USB, select Trust This Computer.

```bash
python -c '
import cv2
for i in range(4):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        print("Camera:", i)
        cap.release()
'
```

Typical: `0` = laptop (musician), `1` = iPhone (conductor).

---

## Running

```bash
# Terminal 1 — MRT2 parameter monitor
python mrt2_mock.py

# Terminal 2 — Musician
python ensemble.py --camera 0

# Terminal 3 — Conductor
python gesture_midi.py --camera 1 --mode beginner

# Terminal 4 — Audience display
python display.py
```

**Reaper:** add MRT2 as AU plugin on track 2, enable OSC receive on port `8000`.

**MRT2 Jam:** MIDI INPUT → select `GestureInstrument`.

---

## Musician Track

| Action | Effect |
|--------|--------|
| Nod ×5 | Establishes BPM, starts Reaper clock |
| Nod 5–12 | Refines BPM (locked after nod 12) |
| Play keyboard | Notes forwarded to MRT2 as melodic conditioning |
| Play harder | Energy floor rises, conductor nudged |

HUD shows: transport state, BPM, beat number, nod progress, conductor style tint.

---

## Conductor Track

### Three modes

| Mode | Controls |
|------|----------|
| Beginner | Start / Stop / Calm / Medium / Intense |
| Assisted | + Follow Performer + Rhythmic Pulse |
| Expert | + Adventure + Style Commitment + Style Preset + Section |

Switch with `1` / `2` / `3` or the on-screen dropdown.

### Keyboard controls

| Key | Action |
|-----|--------|
| `S` | Start on next bar |
| `X` | End on next bar |
| `Space` | Hold / Resume |
| `E` | Emergency stop |
| `C` | Personal calibration |
| `[ ]` | Style preset (Expert) |
| `N` | Next section (Expert) |
| `Q` | Quit |

---

## OSC Ports

| Port | Direction | Content |
|------|-----------|---------|
| 9000 | conductor → performer | musical intentions + actions |
| 9002 | performer → all | BPM, beat, state, tracking |
| 9003 | conductor → performer | style + energy for HUD tint |
| 9004 | performer → conductor | energy floor (soft nudge) |
| 8000 | performer → Reaper | tempo, transport, MRT2 AU params |

---

## Project Structure

```
music-hackathon/
├── accessible_ensemble/
│   ├── core.py          # tempo, musical intent, state machine
│   ├── performer.py     # musician camera, MIDI, Reaper, orchestration
│   ├── conductor.py     # conductor camera, three modes, calibration
│   └── mrt2_mock.py     # MRT2 parameter monitor
├── docs/
├── models/              # MediaPipe models (auto-downloaded)
├── display.py           # unified audience visualization
├── ensemble.py          # → accessible_ensemble.performer
├── gesture_midi.py      # → accessible_ensemble.conductor
└── requirements.txt
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Camera won't open | System Settings → Privacy → Camera → allow Terminal, restart Terminal |
| MRT2 can't see GestureInstrument | Start `ensemble.py` first, then open MRT2 |
| Reaper not syncing | Enable OSC receive on port 8000 in Reaper preferences |
| Nods not detected | Better lighting, face camera directly, nod more deliberately |
| Mock shows stale OSC | Check `ensemble.py` is past WAITING state |
