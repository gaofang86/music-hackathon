# Hardware and Software Test Guide

## 1. Test Goal

Verify the complete accessible ensemble workflow:

- The performer controls BPM with head nods.
- The performer sends MIDI notes to MRT2.
- The conductor is tracked with the iPhone camera.
- The conductor UI supports Beginner, Assisted, and Expert modes.
- Structural actions occur on bar boundaries.
- Reaper, the conductor UI, and the MRT2 control backend remain synchronized.

## 2. Required Hardware

- Apple Silicon Mac
- iPhone with Continuity Camera support
- USB cable for the iPhone
- MIDI keyboard
- External display or projector
- Audio interface and speakers or headphones
- Optional external webcam if the Mac cannot face the performer

## 3. Recommended Physical Layout

```text
External display / projector
  -> Performer UI
  -> Visible to the performer

Laptop display
  -> Conductor UI
  -> Visible to the conductor

Laptop or external webcam
  -> Faces the performer
  -> Detects head nods

iPhone rear camera
  -> Faces the conductor
  -> Detects hands, face, and body
```

The most reliable arrangement uses an external webcam for the performer so the
laptop can remain close to the conductor.

## 4. Display Setup

1. Connect the projector or external display.
2. Open **System Settings > Displays**.
3. Select **Extend Display**. Do not use screen mirroring.
4. Keep `Accessible MRT2 Conductor` on the laptop display.
5. Move `Performer Tempo and Ensemble State` to the external display.
6. Make the performer window full screen.

## 5. iPhone Camera Setup

1. Connect the iPhone to the Mac by USB.
2. Unlock the iPhone.
3. Select **Trust This Computer** if prompted.
4. Keep Wi-Fi and Bluetooth enabled on both devices.
5. Point the iPhone rear camera at the conductor.
6. Mount or support the phone so the image does not move during calibration.

No OSC application is required on the iPhone. The iPhone only supplies video.

## 6. Software Installation

```bash
cd ~/Desktop/music-hackathon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Verify dependencies:

```bash
python -c "import cv2, mediapipe, rtmidi, pythonosc; print('Dependencies OK')"
```

## 7. Camera Detection

Run:

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

Record the camera numbers:

```text
Performer camera: ______
Conductor iPhone camera: ______
```

Typical values are `0` for the laptop camera and `1` for the iPhone.

## 8. MIDI Device Check

List available MIDI inputs:

```bash
python -c '
import rtmidi
midi = rtmidi.MidiIn()
for index, name in enumerate(midi.get_ports()):
    print(index, name)
'
```

Record the keyboard name:

```text
MIDI keyboard: ______________________________
```

## 9. Reaper Setup

1. Open Reaper.
2. Open **Preferences > Control/OSC/web**.
3. Add an OSC control surface using the default OSC pattern.
4. Set its receive port to `8000` and local IP to `127.0.0.1`.
5. Set the project time signature to 4/4.
6. Create or import the audible guide-drum arrangement at project position
   `1.1.00`.
7. Insert a drum instrument or use an audio drum loop, then confirm it produces
   sound when Reaper's Play button is pressed.
8. Keep the drum track unmuted and route it to the correct audio output.
9. `MusicianClock` may remain enabled for clock and generated guide-note
   experiments, but the required fifth-nod test plays the arrangement already
   written in Reaper.

On the fifth detected nod, the project sends Reaper these operations:

```text
Stop -> Go to project start -> Set visible project BPM -> Play
```

The BPM field in Reaper's transport bar must change to the detected smoothed
tempo, and the prepared drum arrangement must be audible immediately.

## 10. MRT2 Setup

### Interaction Test Without Custom MRT2

Use the included visual mock:

```bash
source .venv/bin/activate
python mrt2_mock.py
```

The mock listens on `127.0.0.1:9100` and displays:

- Temperature
- Top-K
- Prompt CFG
- Note CFG
- Drum CFG
- Style
- Section
- Structural actions

### Real MRT2 Test

The stock MRT2 Jam MIDI input only handles Note On and Note Off.

1. Select `GestureInstrument` as the MIDI input in MRT2 Jam.
2. Use the custom MRT2 Jam build with the local OSC bridge on port `9100` for
   parameter and transport control.
3. Do not expect stock Jam to respond to the old CC20-25 control scheme.

## 11. Start the System

Open three terminals.

### Terminal 1: MRT2 Backend Test

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python mrt2_mock.py
```

### Terminal 2: Performer Controller

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python ensemble.py \
  --camera PERFORMER_CAMERA \
  --midi-port "MIDI KEYBOARD NAME"
```

Example:

```bash
python ensemble.py --camera 0 --midi-port "KeyLab"
```

### Terminal 3: Conductor UI

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate
python gesture_midi.py \
  --camera IPHONE_CAMERA \
  --mode beginner \
  --input auto \
  --profile test-user
```

Example:

```bash
python gesture_midi.py --camera 1 --mode beginner --profile test-user
```

## 12. Calibration Test

1. Press `C` in the conductor UI.
2. Remain naturally still for three seconds.
3. Move through a comfortable range for five seconds.
4. Confirm that `CALIBRATION SAVED` appears.
5. Confirm that `profiles/test-user.json` was created.
6. Restart the conductor UI with the same profile.
7. Confirm that the saved range is reused.

Test separately with:

```bash
--input hands
--input face
--input body
```

The participant should choose the input method. The system must not infer a
disability from failed hand detection.

## 13. Performer Tempo Test

1. Confirm the performer camera shows the performer's face.
2. Nod five times at a steady tempo.
3. Confirm the state changes from `WAITING` to `READY`.
4. Confirm the displayed BPM is close to the intended tempo.
5. Confirm Reaper starts its guide drum track.
6. Confirm the beat display cycles through `1 2 3 4`.
7. Continue nodding through nod 12 and confirm `TEMPO LOCKED` appears.
8. Change nod speed after nod 12 and confirm Reaper's BPM no longer changes.
9. Press `R` and confirm tempo learning starts again from zero.
10. Repeat with `--nods-to-start 4`.
11. Repeat with deliberately uneven nods and confirm BPM smoothing.

## 14. Beginner Mode Test

Start with:

```bash
python gesture_midi.py --camera 1 --mode beginner
```

Verify:

- Only Energy and structural controls are emphasized.
- Energy is interpreted as Calm, Medium, or Intense.
- A valid start action changes `READY` to `ARMED`.
- A countdown appears.
- The state changes to `ACTIVE` on beat 1.
- A normal stop changes the state to `STOP QUEUED`.
- The mock receives a one-bar volume ramp.
- The state returns to `READY` after the fade bar.

## 15. Assisted Mode Test

Press `2` or start with `--mode assisted`.

Verify:

- `Follow Performer` is visible.
- `Rhythmic Pulse` is visible.
- Horizontal movement changes Follow.
- Vertical movement changes Pulse.
- Parameter movement is smooth rather than abrupt.
- The MRT2 mock receives changes to `cfg_notes` and `cfg_drums`.

## 16. Expert Mode Test

Press `3` or start with `--mode expert`.

Verify:

- Adventure is visible.
- Style Commitment is visible.
- Style preset and Section are visible.
- `[` and `]` change style presets.
- `N` advances the section.
- The MRT2 mock receives temperature, Top-K, style CFG, style, and section.
- All values stay inside the documented performance-safe ranges.

## 17. HOLD and Tracking-Loss Test

1. Start an active section.
2. Press `Space`.
3. Confirm the state becomes `HOLD`.
4. Move in front of the camera.
5. Confirm musical intention values remain frozen.
6. Press `Space` again and confirm `ACTIVE`.
7. Block the iPhone camera or leave the frame.
8. Confirm the UI reports tracking loss.
9. Confirm the system enters HOLD instead of resetting values to zero.

## 18. Stop Tests

### Normal Stop

1. While active, press `X`.
2. Confirm `STOP QUEUED`.
3. Confirm a countdown to the next bar.
4. Confirm the fade begins on beat 1.
5. Confirm the fade lasts one bar.
6. Confirm the final state is `READY`.

### Emergency Stop

1. Start another active section.
2. Press `E`.
3. Confirm immediate `EMERGENCY STOP`.
4. Confirm the mock receives volume `-60 dB` and bypass `1`.

## 19. MIDI Prompt Test

1. Select `GestureInstrument` in MRT2 Jam.
2. Play the MIDI keyboard.
3. Confirm MRT2 Jam displays the active notes.
4. Confirm only Note On and Note Off are forwarded.
5. Confirm conductor actions do not create MIDI notes.

## 20. Automated Tests

```bash
cd ~/Desktop/music-hackathon
source .venv/bin/activate

python -m unittest discover -v -s tests
python -m py_compile \
  ensemble.py \
  gesture_midi.py \
  mrt2_mock.py \
  musician.py \
  accessible_ensemble/*.py
```

Expected result:

```text
Ran 10 tests
OK
```

## 21. Acceptance Checklist

### Hardware

- [ ] Laptop or external camera reliably tracks the performer
- [ ] iPhone camera reliably tracks the conductor
- [ ] External display is visible to the performer
- [ ] Laptop display is visible to the conductor
- [ ] MIDI keyboard is detected
- [ ] Audio output is stable

### Timing

- [ ] BPM is established from performer head nods
- [ ] Beat display remains aligned with Reaper
- [ ] Start occurs on beat 1
- [ ] Normal stop fade begins on beat 1
- [ ] No unintended structural action occurs

### Accessibility

- [ ] Participant can select a preferred input source
- [ ] Calibration completes without developer intervention
- [ ] UI can be understood without hearing audio
- [ ] HOLD allows the conductor to rest
- [ ] Tracking loss preserves the current musical state
- [ ] Emergency stop is easy to reach

### Interaction Modes

- [ ] Beginner mode presents only essential concepts
- [ ] Assisted mode adds two understandable continuous controls
- [ ] Expert mode exposes advanced musical direction
- [ ] Technical parameter names are not required during performance

## 22. Test Notes

```text
Date:
Tester:
Performer:
Conductor:
Mac model:
macOS version:
iPhone model:
Camera numbers:
MIDI keyboard:
Reaper version:
MRT2 version:

Observed latency:
False start count:
False stop count:
Tracking-loss events:
Audio underruns:

Notes:
```
