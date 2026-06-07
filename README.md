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
│   └── mrt2_mock.py         # Visual mock of MRT2 parameter mappings
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
  -> Reaper track containing Google: MRT2 AU

iPhone Camera -> gesture_midi.py
  -> Personal calibration
  -> Beginner / Assisted / Expert musical intentions
  -> 127.0.0.1:9000
  -> ensemble.py state machine
  -> Reaper OSC 127.0.0.1:8000
  -> MRT2 AU automation parameters
```

The performance setup uses the **Google: MRT2 AUv3 instrument hosted inside
Reaper**. MRT2 Jam is no longer used. Performer notes enter the MRT2 track
through `GestureInstrument`; conductor parameters are sent to Reaper's FX
automation OSC addresses.

## Installation

```bash
cd ~/Desktop/music-hackathon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Cameras

Connect and unlock the iPhone by USB, select **Trust This Computer**, and keep Wi-Fi and Bluetooth enabled.

On first use, allow the terminal application to access cameras under
**System Settings > Privacy & Security > Camera**. Restart the terminal after
changing this permission.

Detect the camera options:

```bash
python ensemble.py --list-cameras
```

Camera numbers are machine-specific. On one Mac, `camera 0` may be the iPhone;
on another Mac, `camera 0` may be the laptop camera. Assign cameras by role:

```text
Laptop / built-in / external webcam -> Performer
iPhone Continuity Camera            -> Conductor
```

## Test Run

### 1. Start Reaper with MRT2 AU

Install and register `MRT2 (AU).app`, set Reaper to 48 kHz, and load
**AUv3i: Google: MRT2** as the first FX on track 2. Set that track's MIDI input
to `GestureInstrument`, enable record monitoring, and keep the track unmuted.

### 2. Start the Performer Controller

Terminal 2:

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python ensemble.py
```

The app lists available cameras and asks which one should be used for the
performer. When multiple cameras are available, it also displays numbered
camera previews so the correct role can be identified visually. For a fixed
show setup, pass the known index explicitly, for example
`python ensemble.py --camera 0`.

With a MIDI keyboard:

```bash
python ensemble.py --midi-port "Keyboard Name"
```

By default, the performer nods five times to establish the tempo. The final nod is beat 1 of the first bar. Tempo refinement continues through the twelfth valid nod. The twelfth nod sets the final smoothed BPM; all later nods are ignored so ordinary head movement cannot keep changing Reaper's tempo. Press `R` in the performer window to reset the session and learn a new tempo.

For Reaper playback, configure a local OSC control surface on receive port
`8000` using the default OSC pattern. Prepare an audible drum arrangement at
project position `1.1.00`. On the fifth nod, the system stops Reaper, returns
to the project start, writes the smoothed BPM into Reaper's visible tempo
field, and starts playback.

The default MRT2 location is Reaper track 2, FX slot 1. Override it when
needed:

```bash
python ensemble.py --mrt2-track 3 --mrt2-fx 1
```

For parameter testing without the AU:

```bash
python mrt2_mock.py
python ensemble.py --mrt2-backend mock
```

### 3. Start the Conductor Interface

Terminal 3:

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python gesture_midi.py --mode beginner --input auto
```

Choose the conductor's iPhone camera from the startup menu. For a fixed show
setup, pass the known index explicitly, for example
`python gesture_midi.py --camera 1 --mode beginner --input auto`.

You can explicitly select the body input:

```bash
python gesture_midi.py --input hands
python gesture_midi.py --input face
python gesture_midi.py --input body
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

MRT2 AU through Reaper:

```text
127.0.0.1:8000

/track/TRACK/fx/FX/fxparam/PARAM/value
/track/TRACK/fx/FX/bypass
```

Bidirectional nudge (internal):

```text
127.0.0.1:9003  conductor → performer
  /conductor/style    (HUD background tint)
  /conductor/energy   (current energy level)

127.0.0.1:9004  performer → conductor
  /performer/energy_floor  (soft minimum from playing intensity)
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
