# Accessible MRT2 Ensemble

The performer establishes the BPM through head nods captured by the laptop camera and provides musical content through a MIDI keyboard. The conductor uses the iPhone Continuity Camera to control musical structure and direction.

The iPhone only supplies video. No OSC app is required on the phone. All recognition and OSC communication run locally on the Mac.

See [CONDUCTOR_DESIGN.md](docs/CONDUCTOR_DESIGN.md) for the complete design specification.
See [TEST_GUIDE.md](docs/TEST_GUIDE.md) for the full hardware and software testing procedure.

## Project Structure

```text
music-hackathon/
├── accessible_ensemble/     # Application implementation
│   ├── core.py              # Tempo, musical intentions, and state machine
│   ├── performer.py         # Performer camera, MIDI, Reaper, and orchestration
│   ├── conductor.py         # Calibration and three-mode conductor UI
│   └── mrt2_mock.py         # Visual mock of the custom MRT2 OSC bridge
├── docs/                    # Design and hardware/software test guides
├── models/                  # MediaPipe model assets
├── scripts/                 # Optional setup utilities
├── tests/                   # Automated tests
├── ensemble.py              # Performer compatibility entry point
├── gesture_midi.py          # Conductor compatibility entry point
├── mrt2_mock.py             # Mock-backend compatibility entry point
├── musician.py              # Legacy performer alias
└── requirements.txt
```

The root entry points keep the existing commands stable. New implementation
code should be added under `accessible_ensemble/`.

## Current Architecture

```text
Laptop Camera -> ensemble.py
  -> Performer head-controlled BPM
  -> Reaper / MusicianClock

MIDI Keyboard -> ensemble.py
  -> GestureInstrument Note On/Off
  -> MRT2 Jam note conditioning

iPhone Camera -> gesture_midi.py
  -> Personal calibration
  -> Beginner / Assisted / Expert musical intentions
  -> 127.0.0.1:9000
  -> ensemble.py state machine
  -> 127.0.0.1:9100
  -> Custom MRT2 Jam OSC Bridge
```

The stock MRT2 Jam MIDI input only handles Note On and Note Off. It does not process the previously used CC20-25 controls, MIDI Clock, or Start/Stop messages. Therefore:

- Performer notes can still enter stock Jam through `GestureInstrument`.
- Conductor parameters require the custom Jam OSC Bridge.
- Until that bridge is implemented, use `mrt2_mock.py` to test the complete interaction.

## Installation

```bash
cd ~/Desktop/music-hackathon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Cameras

Connect and unlock the iPhone by USB, select **Trust This Computer**, and keep Wi-Fi and Bluetooth enabled.

Detect the camera numbers:

```bash
python -c '
import cv2
for i in range(8):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        print("Available camera:", i)
        cap.release()
'
```

Typical assignments:

```text
camera 0 = Laptop = Performer
camera 1 = iPhone = Conductor
```

## Test Run

### 1. Start the MRT2 Mock Backend

Terminal 1:

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python mrt2_mock.py
```

It displays `temperature`, `top_k`, the three CFG values, style, and structural actions.

### 2. Start the Performer Controller

Terminal 2:

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python ensemble.py --camera 0
```

With a MIDI keyboard:

```bash
python ensemble.py --camera 0 --midi-port "Keyboard Name"
```

By default, the performer nods five times to establish the tempo. The final nod is beat 1 of the first bar. Tempo refinement continues through the twelfth valid nod. The twelfth nod sets the final smoothed BPM; all later nods are ignored so ordinary head movement cannot keep changing Reaper's tempo. Press `R` in the performer window to reset the session and learn a new tempo.

For Reaper playback, configure a local OSC control surface on receive port
`8000` using the default OSC pattern. Prepare an audible drum arrangement at
project position `1.1.00`. On the fifth nod, the system stops Reaper, returns
to the project start, writes the smoothed BPM into Reaper's visible tempo
field, and starts playback.

### 3. Start the Conductor Interface

Terminal 3:

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python gesture_midi.py --camera 1 --mode beginner --input auto
```

You can explicitly select the body input:

```bash
python gesture_midi.py --camera 1 --input hands
python gesture_midi.py --camera 1 --input face
python gesture_midi.py --camera 1 --input body
```

The system does not infer a participant's abilities from a failed hand detection. For formal use, explicitly select the participant's preferred input method.

## Three Conductor Modes

The conductor window includes a clickable mode dropdown for **Beginner**,
**Assisted**, and **Expert**. Keyboard shortcuts `1`, `2`, and `3` remain
available. The camera view overlays detected hand skeletons, facial features,
and body pose connections regardless of which input source currently controls
the musical parameters.

### Beginner

Displays and controls only:

- Start on the next bar
- Calm / Medium / Intense energy levels
- End on the next bar
- HOLD and emergency stop

```bash
python gesture_midi.py --mode beginner
```

### Assisted

Adds two continuous musical dimensions:

- `Follow Performer`: closely follow the performer or move more freely
- `Rhythmic Pulse`: more melodic or more rhythmic

```bash
python gesture_midi.py --mode assisted
```

### Expert

Also adds:

- Adventure
- Style Commitment
- Style Preset
- Section

```bash
python gesture_midi.py --mode expert
```

Press `1`, `2`, or `3` during operation to switch modes.

## Conductor UI

The interface always displays:

- `WAITING / READY / ARMED / ACTIVE / HOLD / STOP QUEUED`
- Current beat in 4/4
- Start or stop countdown
- BPM
- Tracking status
- Current mode and input source
- Current musical intentions

Keyboard fallback controls:

| Key | Action |
|---|---|
| `1/2/3` | Beginner / Assisted / Expert |
| `C` | Start personal calibration |
| `S` | Start on the next bar |
| `X` | End normally on the next bar |
| `Space` | HOLD / Resume |
| `E` | Immediate emergency stop |
| `[` / `]` | Expert style preset |
| `N` | Expert next section |
| `Q` | Quit |

## Personal Calibration

Press `C`:

1. Remain naturally still for three seconds to record noise and the neutral position.
2. Move through a comfortable range for five seconds.
3. The result is saved to `profiles/<profile>.json`.

Specify a profile:

```bash
python gesture_midi.py --profile alice --input body
```

The calibrated relative movement range is mapped to `0..1`, instead of relying on universal screen-distance thresholds.

## State Machine

```text
WAITING -> READY -> ARMED -> ACTIVE
                           -> HOLD -> ACTIVE
                           -> STOP_QUEUED -> READY
ANY -> EMERGENCY_STOP
```

- A normal start takes effect on the next bar.
- A normal ending begins a one-bar fade on the next bar.
- HOLD freezes musical intentions and allows the conductor to rest.
- Tracking loss automatically enters HOLD without moving parameters toward zero.
- Emergency Stop immediately mutes and bypasses MRT2.

## Musical Intention Mapping

Performance-safe ranges:

| Musical Intention | MRT2 |
|---|---|
| Energy | `temperature 0.8-1.55`, `top_k 24-140` |
| Follow Performer | `cfg_notes 0.8-4.2` |
| Rhythmic Pulse | `cfg_drums 1.0-4.5` |
| Style Commitment | `cfg_musiccoca 0.8-4.0` |
| Style | Precomputed style preset |

`cfg_drums` is fully meaningful only when the custom backend supplies real drum conditioning.

## Local OSC

Conductor intentions:

```text
127.0.0.1:9000

/conductor/action/start
/conductor/action/hold
/conductor/action/resume
/conductor/action/stop
/conductor/action/emergency_stop
/conductor/energy
/conductor/follow
/conductor/pulse
/conductor/adventure
/conductor/style_commitment
/conductor/style
/conductor/section
/conductor/mode
/conductor/tracking
```

Custom Jam Bridge:

```text
127.0.0.1:9100

/mrt2/temperature
/mrt2/top_k
/mrt2/cfg_musiccoca
/mrt2/cfg_notes
/mrt2/cfg_drums
/mrt2/style
/mrt2/section
/mrt2/volume
/mrt2/volume_ramp
/mrt2/bypass
/mrt2/action/prepare
/mrt2/action/start
/mrt2/action/hold
/mrt2/action/stop_queued
```

## Automated Tests

```bash
python -m unittest discover -v -s tests
python -m py_compile \
  ensemble.py \
  gesture_midi.py \
  mrt2_mock.py \
  musician.py \
  accessible_ensemble/*.py
```
