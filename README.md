# Accessible MRT2 Ensemble

A two-track live performance system. The musician establishes tempo through head nods and provides musical content via MIDI keyboard. The conductor shapes the AI-generated background using body movement and facial expressions — no music knowledge required.

See [CONDUCTOR_DESIGN.md](docs/CONDUCTOR_DESIGN.md) for the full design specification.
See [TEST_GUIDE.md](docs/TEST_GUIDE.md) for hardware and software testing procedures.

---

## Architecture

```
MUSICIAN                              CONDUCTOR
laptop cam (index 0)                  iPhone cam (index 1)
    │                                     │
 Head nods ×5 → BPM established      Hands / face / body
    │                                     │
    ├── MIDI Clock → Reaper           MusicalIntent
    │   (beat sync + kick/snare/hat)  energy / style / pulse
    │                                     │
    ├── MIDI notes → MRT2             OSC → 127.0.0.1:9000
    │   (melodic conditioning)             │
    │                                      ▼
    │                              EnsembleController
    │                              WAITING→READY→ARMED→ACTIVE
    │                                      │
    │                              Mrt2OscAdapter → 127.0.0.1:9100
    │                              temperature / cfg_notes / cfg_drums
    │                                      │
    └──────── bidirectional nudge ─────────┘
         Musician velocity → energy floor → conductor nudge
         Conductor style   → performer HUD background tint

              ↓                    ↓
         Reaper audio         MRT2 audio
         (musician's          (AI-generated
          real sound)          accompaniment)
              └────────────────────┘
                    speakers
```

### OSC ports

| Port | Direction | Content |
|------|-----------|---------|
| 9000 | conductor → performer | musical intentions + actions |
| 9002 | performer → conductor | feedback (BPM, beat, state) |
| 9003 | conductor → performer | style + energy for HUD tint |
| 9004 | performer → conductor | energy floor (soft nudge) |
| 9100 | performer → MRT2 bridge | generation parameters |

> **Note:** MRT2 Jam's stock MIDI input only handles Note On/Off. The 9100 bridge is currently mocked by `mrt2_mock.py`. Conductor parameters have no effect on real MRT2 until the OSC bridge is implemented.

---

## Project Structure

```
music-hackathon/
├── accessible_ensemble/
│   ├── core.py          # tempo, musical intent, state machine
│   ├── performer.py     # musician camera, MIDI, Reaper, ensemble orchestration
│   ├── conductor.py     # conductor camera, three interaction modes, calibration
│   └── mrt2_mock.py     # visual mock of the MRT2 OSC bridge
├── docs/
│   ├── CONDUCTOR_DESIGN.md
│   └── TEST_GUIDE.md
├── models/              # MediaPipe model assets (auto-downloaded)
├── ensemble.py          # entry point → accessible_ensemble.performer
├── gesture_midi.py      # entry point → accessible_ensemble.conductor
├── mrt2_mock.py         # entry point → accessible_ensemble.mrt2_mock
└── requirements.txt
```

---

## Setup

```bash
cd ~/Desktop/music-hackathon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Cameras

Connect iPhone via USB, select **Trust This Computer**, keep Wi-Fi and Bluetooth on.

```bash
python -c '
import cv2
for i in range(4):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        print("Camera available:", i)
        cap.release()
'
```

Typical: `0` = laptop, `1` = iPhone.

---

## Running

Open three terminals:

**Terminal 1 — MRT2 mock (start first)**
```bash
source .venv/bin/activate
python mrt2_mock.py
```

**Terminal 2 — Musician**
```bash
source .venv/bin/activate
python ensemble.py --camera 0
# with MIDI keyboard:
python ensemble.py --camera 0 --midi-port "Keyboard Name"
```

Nod 5 times at your desired tempo. The 5th nod starts the clock and Reaper.

**Terminal 3 — Conductor**
```bash
source .venv/bin/activate
python gesture_midi.py --camera 1 --mode beginner
```

Then in **MRT2 Jam → MIDI INPUT → GestureInstrument**.

---

## Musician Track

### Tempo
- Nod 5 times → BPM established → MIDI clock starts → Reaper syncs
- Confidence bar appears after 5th nod, fades after 3 seconds
- `r` to reset, `q` to quit

### MIDI keyboard
- Notes forwarded directly to MRT2 as melodic conditioning
- Playing velocity tracked → soft energy floor sent to conductor

### Performer HUD
- Top bar: transport state, BPM, beat number, nod count
- Bottom strip: background tint reflects conductor's current style
  - Warm Acoustic → orange, Dark Cinematic → deep blue, Bright Electronic → cyan

### Reaper sync
- `MusicianClock` port carries MIDI Clock + kick/snare/hihat
- OSC `/tempo/raw` keeps Reaper's grid aligned
- Set Reaper time signature to 4/4, slave transport to `MusicianClock`

---

## Conductor Track

### Three modes

| Mode | Available controls |
|------|--------------------|
| Beginner | Start / Stop / Calm / Medium / Intense |
| Assisted | + Follow Performer + Rhythmic Pulse |
| Expert | + Adventure + Style Commitment + Style Preset + Section |

Switch with keys `1` / `2` / `3`.

### Bidirectional nudge
- Musician plays hard (high velocity) → energy floor rises → conductor feels resistance below ~0.4
- Conductor sets style → performer HUD tint changes → musician sees it in peripheral vision

### State machine
```
WAITING → READY → ARMED → ACTIVE
                         → HOLD → ACTIVE
                         → STOP_QUEUED → READY
ANY → EMERGENCY_STOP
```
Start and stop always take effect on the next bar boundary.

### Keyboard controls

| Key | Action |
|-----|--------|
| `1/2/3` | Beginner / Assisted / Expert |
| `C` | Personal calibration |
| `S` | Start on next bar |
| `X` | End on next bar |
| `Space` | Hold / Resume |
| `E` | Emergency stop |
| `[` / `]` | Style preset (Expert) |
| `N` | Next section (Expert) |
| `Q` | Quit |

---

## Musical Intent → MRT2 Parameters

| Intent | MRT2 parameter | Range |
|--------|---------------|-------|
| Energy | temperature + top_k | 0.8–1.55 / 24–140 |
| Follow Performer | cfg_notes | 0.8–4.2 |
| Rhythmic Pulse | cfg_drums | 1.0–4.5 |
| Style Commitment | cfg_musiccoca | 0.8–4.0 |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Camera won't open | System Settings → Privacy → Camera → allow Terminal, restart Terminal |
| MRT2 can't see GestureInstrument | Start `gesture_midi.py` first, then open MRT2 |
| Reaper can't see MusicianClock | Start `ensemble.py` first, then open Reaper |
| mock shows stale OSC timestamp | Check ensemble.py is running and past WAITING state |
| Nods not detected | Improve lighting, face camera squarely, nod more deliberately |
