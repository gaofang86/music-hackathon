# Accessible Conductor Design for Magenta RealTime 2

## 1. Product Position

This system is a live ensemble, not a body-to-parameter demo.

- The performer owns tempo through head motion and supplies MIDI content.
- The conductor owns form, musical direction, and how MRT2 responds.
- MRT2 supplies a continuously generated audio voice.
- Reaper owns the shared bar grid, guide track, mixing, and final output.

The conductor should manipulate musical intentions. Model parameters are an
implementation detail and must not appear in the default interface.

## 2. Deployment Decision

### Recommended: a fork of the official MRT2 Jam app

Add a loopback-only OSC bridge beside Jam's existing `RealtimeRunner`.

```text
iPhone camera
  -> gesture_midi.py
  -> calibrated conductor intentions
  -> local OSC
  -> custom MRT2 Jam OSC bridge
  -> RealtimeRunner setters
  -> MRT2 audio
```

Reasons:

1. Jam already supplies the real-time C++ audio engine, MIDI note input,
   model loading, prompts, monitoring, and UI.
2. `RealtimeRunner` exposes setters for temperature, top-k, CFG values,
   drumless mode, bypass, reset, volume, prompts, and note state.
3. The stock Jam MIDI callback accepts note on/off but does not map CC,
   MIDI Clock, Start, or Stop to model controls.
4. Python `MagentaRT2SystemMlxfn` is valuable for experiments, but a live
   audio callback, buffering, device management, and underrun recovery would
   need to be rebuilt.

The AU/Reaper route remains a useful second backend, but it needs a reliable
OSC-to-AU automation layer and should not be the first implementation.

## 3. Control Architecture

The system uses four layers. No layer may be skipped.

```text
BODY SIGNAL
  landmarks, switches, breath, shoulder, head, gaze
       |
       v
PERSONAL CALIBRATION
  neutral, usable range, noise, hold duration, preferred gestures
       |
       v
MUSICAL INTENTION
  start, end, hold, energy, follow, pulse, adventure, style, section
       |
       v
MRT2 ADAPTER
  cfg_notes, cfg_drums, cfg_musiccoca, temperature, top_k, style tokens,
  volume/bypass and audio fade scheduling
```

Raw landmarks must never directly set MRT2 parameters.

## 4. Musical Intention Model

All continuous values are normalized to `0..1`.

### Energy

Perceived activity and force, not raw movement speed.

```text
temperature     0.80 -> 1.45
top_k             30 -> 120
cfg_drums        2.0 -> 4.0
output gain     -6dB -> 0dB, optional
```

Use a curved mapping so the quiet half has more precision. Do not expose the
full engine range during a performance.

### Follow

How closely MRT2 follows the performer's MIDI notes.

```text
follow 0.0 -> cfg_notes 0.8
follow 1.0 -> cfg_notes 4.2
```

This must change slowly. A sudden jump can make the generated part feel broken
rather than responsive.

### Pulse

How firmly MRT2 follows the shared rhythmic guide.

```text
pulse 0.0 -> cfg_drums 1.0
pulse 1.0 -> cfg_drums 4.5
```

This requires the custom runner to supply meaningful drum conditioning from the
shared beat grid. Changing `cfg_drums` without drum conditioning is not enough.

### Adventure

How surprising the continuation may become.

```text
adventure 0.0 -> temperature 0.75, top_k 24
adventure 0.5 -> temperature 1.10, top_k 55
adventure 1.0 -> temperature 1.55, top_k 140
```

Temperature and top-k form one musical control. Beginners should never have to
understand or control them separately.

### Style Commitment

How strongly MRT2 follows the selected style.

```text
style_commitment 0.0 -> cfg_musiccoca 0.8
style_commitment 1.0 -> cfg_musiccoca 4.0
```

### Style Direction

Choose between precomputed style anchors. Do not run MusicCoCa text embedding
for every video frame.

At load time:

1. Embed and tokenize a small curated style set.
2. Give each style a clear color, name, and tactile/visual identity.
3. During performance, select a style or interpolate using the engine's prompt
   blend controls.
4. Apply large style changes at section boundaries.

Example anchors:

```text
warm acoustic
minimal pulse
bright electronic
dark cinematic
percussive experimental
```

## 5. Structural State Machine

```text
WAITING
  Tempo is not established. Conductor controls are visible but cannot start.

READY
  Tempo and bar grid exist. Waiting for conductor start intent.

ARMED
  Start accepted. UI shows countdown to the next bar.

ACTIVE
  MRT2 is audible and continuous controls are accepted.

HOLD
  Current values are frozen. The conductor may rest without parameter drift.

STOP_QUEUED
  Normal stop accepted. Fade begins at the next bar.

EMERGENCY_STOP
  Immediate mute/bypass. Reserved for safety and technical failure.
```

Transitions:

```text
WAITING -> READY          performer establishes tempo
READY -> ARMED            conductor confirms start
ARMED -> ACTIVE           next bar boundary
ACTIVE -> HOLD            hold gesture or switch
HOLD -> ACTIVE            resume gesture
ACTIVE/HOLD -> STOP_QUEUED normal end gesture
STOP_QUEUED -> READY      next bar fade completes
ANY -> EMERGENCY_STOP     dedicated emergency action
```

Normal stop must not be immediate. It should use a one-bar equal-power fade.

## 6. Interaction Modes

### Beginner

Three concepts only:

- Start at next bar.
- Energy: calm, medium, intense.
- End at next bar.

Style is selected before playing. Follow, pulse, and adventure use a tested
preset.

### Assisted

Adds two continuous axes:

- Horizontal: follow performer <-> move freely.
- Vertical: melodic <-> rhythmic.

Energy still comes from movement magnitude or another chosen input.

### Expert

Exposes:

- Energy
- Follow
- Pulse
- Adventure
- Style commitment
- Style direction
- Section cue

Technical MRT2 parameters may be visible in a diagnostics panel, not the main
performance UI.

## 7. Personal Calibration

Calibration is mandatory and should take less than two minutes.

### Step 1: Select intentional input channels

The participant chooses, with preview:

- One hand or two hands
- Eyebrow
- Head tilt or head turn
- Shoulder movement
- Torso center
- Mouth gesture
- External switch or breath sensor

The system must not infer a disability or automatically assign mouth controls
because hands are absent.

### Step 2: Record neutral signal

Record 5 seconds at rest:

- Mean position
- Natural noise
- Tracking confidence
- Involuntary movement range

### Step 3: Record comfortable range

Ask for a small and a comfortable maximum movement. Map this personal range to
`0..1`; never use a universal screen-distance threshold.

### Step 4: Choose confirmation method

- Hold
- Double action
- Dwell in a target zone
- External switch

Start and stop should use confirmation. Continuous controls should not.

### Step 5: Preview consequences

Show the interpreted intention without changing music. The participant confirms
that calm/intense, follow/free, start, hold, and stop are distinguishable.

Save calibration as a named profile.

## 8. Signal Processing Rules

1. Apply confidence gating before smoothing.
2. Normalize using the personal calibration range.
3. Use hysteresis around discrete gesture thresholds.
4. Smooth continuous values with attack and release rates:
   - Attack: 150-300 ms
   - Release: 400-900 ms
5. Rate-limit engine updates to 10-20 Hz.
6. Quantize beginner-mode energy to three levels at bar boundaries.
7. Let assisted/expert controls update continuously, but slew-limit values.
8. Freeze values when tracking is lost; never drift toward zero unexpectedly.
9. After a longer tracking loss, enter HOLD and show a visible warning.

## 9. Visual Feedback

The conductor display must work without audio.

Always show:

- Current state
- Beat number `1 2 3 4`
- Bar flash on beat 1
- Countdown when start or stop is queued
- Current section
- Energy level
- Whether tracking is reliable
- Confirmation that an action was accepted

Use color plus shape/text. Do not rely on color alone.

Recommended state language:

```text
WAITING FOR TEMPO
READY
START IN 3 BEATS
PLAYING
HOLDING YOUR SETTINGS
END IN 2 BEATS
TRACKING LOST - MUSIC HELD
```

## 10. Bar and Audio Scheduling

MRT2 produces continuous audio and does not become bar-synchronous merely by
receiving MIDI Clock.

The timing layer must:

1. Maintain the performer's 4/4 bar grid.
2. Keep MRT2 inference warm before the audible entrance.
3. Start an output gain ramp exactly at the scheduled bar boundary.
4. Keep MIDI notes and drum conditioning timestamped against the same clock.
5. Queue style changes for the next section boundary.
6. Fade normal stops over one bar.

Recommended behavior:

```text
ARMED:
  model generating silently

next bar:
  gain -inf -> target over 50-150 ms

STOP_QUEUED:
  keep generating

next bar:
  equal-power fade over one bar
  then bypass or stop inference if desired
```

This avoids model warm-up latency becoming musical latency.

## 11. Local OSC Contract

Camera recognition sends intentions, not model parameters:

```text
/conductor/action/start
/conductor/action/hold
/conductor/action/resume
/conductor/action/stop
/conductor/action/emergency_stop

/conductor/energy            float 0..1
/conductor/follow            float 0..1
/conductor/pulse             float 0..1
/conductor/adventure         float 0..1
/conductor/style_commitment  float 0..1
/conductor/style             int preset_index
/conductor/section           int section_index
```

The MRT2 Jam fork receives an internal engine contract:

```text
/mrt2/temperature
/mrt2/top_k
/mrt2/cfg_musiccoca
/mrt2/cfg_notes
/mrt2/cfg_drums
/mrt2/prompt
/mrt2/volume
/mrt2/bypass
/mrt2/reset
```

Only the semantic contract is stable for sensors and accessibility devices.

## 12. Failure Handling

- Camera loss: freeze parameters, then enter HOLD.
- Performer tempo loss: preserve the last stable grid and warn visually.
- MRT2 underrun: keep Reaper and guide rhythm running; mute MRT2 cleanly.
- OSC loss: keep last values; do not stop music.
- MIDI keyboard loss: reduce note-following gradually rather than forcing
  silence.
- Emergency stop: local keyboard shortcut and accessible external switch.

## 13. Evaluation

### Technical

- Action-to-visual-confirmation latency below 100 ms.
- Continuous-control update latency below 150 ms.
- Audible bar entrance error below 20 ms.
- No unintended stop in a 20-minute session.
- No parameter jumps after temporary tracking loss.

### Accessibility

- Participant completes calibration without developer intervention.
- Participant can correctly predict each action's musical consequence.
- At least 90% intentional start/stop recognition.
- Less than one false structural cue per 10 minutes.
- Participant can rest using HOLD without changing the music.

### Musical

- Performer can identify when follow and pulse controls change.
- Style transitions sound intentional rather than abrupt.
- Normal endings preserve phrase structure.
- The conductor reports meaningful authorship rather than indirect influence.

## 14. Implementation Phases

### Phase 1: Honest control path

- Remove CC20-25 claims from the stock Jam integration.
- Add semantic intention objects and the new state machine.
- Implement beginner mode, calibration profiles, and visual feedback.
- Fork Jam and add loopback OSC setters.
- Implement output gain scheduling for bar-aligned start/end.

### Phase 2: Expressive control

- Add assisted mode.
- Add precomputed style anchors.
- Add drum conditioning from the shared beat grid.
- Add HOLD and tracking-loss behavior.

### Phase 3: Expert and research

- Add expert mode and diagnostics.
- Add audio-prompt style anchors.
- Compare gesture mappings in user studies.
- Add optional Python `MagentaRT2SystemMlxfn` backend for experiments.

## 15. First Performance Preset

Use conservative defaults:

```text
temperature       1.05
top_k             50
cfg_musiccoca     1.6
cfg_notes         2.4
cfg_drums         3.0
```

Beginner energy mapping:

| Energy | Temperature | Top-K | CFG Drums |
|---|---:|---:|---:|
| Calm | 0.85 | 30 | 2.0 |
| Medium | 1.05 | 50 | 3.0 |
| Intense | 1.35 | 100 | 4.0 |

These are starting points for listening tests, not universal accessibility
defaults.
