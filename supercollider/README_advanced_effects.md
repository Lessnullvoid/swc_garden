# Advanced Effects -- Multi-Algorithm FX Processor

Technical reference for the `advanced_effects` SuperCollider module (`gardenMultiFX` SynthDef).

## Overview

A multi-algorithm FX processor (design inspired by multi-algorithm pedals such as Hologram Microcosm and Chase Bliss Mood). Offers 6 selectable algorithms with a unified macro-parameter interface. The `algo` parameter scans smoothly through algorithms via `SelectX.ar`, enabling continuous morphing between neighboring effects.

## Signal Flow

```
PlayBuf (mono, looped)
    |
  Pan2 (center stereo)
    |
  +--------------------+
  | Algorithm Bank     |
  | SelectX.ar(algo):  |
  |  0: Hall Reverb    |
  |  1: Ping-Pong Delay|
  |  2: Tape Echo      |
  |  3: Chorus         |
  |  4: Overdrive      |
  |  5: Freezer        |
  +--------------------+
    |
  XFade2 (dry/wet mix)
    |
  Mid-Side width
    |
  LeakDC
    |
  Limiter (0.95)
    |
  Out (stereo)
```

## Algorithm Descriptions

### 0 -- Hall Reverb
Large-space reverb using `FreeVerb2` with optional pre-delay.
- p1: room size (0.3 -- 0.98)
- p2: damping (0.1 -- 0.8)
- p3: pre-delay amount (0 -- 80 ms)

### 1 -- Ping-Pong Delay
Stereo alternating delay with cross-feedback using two `CombC` lines.
- p1: delay time (50 ms -- 1.2 s, exponential)
- p2: feedback (0.05 -- 0.85)
- p3: stereo cross amount (-1 to +1)

### 2 -- Tape Echo
Filtered delay with LFO wow/flutter modulation for analog character.
- p1: echo time (80 ms -- 0.8 s, exponential)
- p2: feedback (0.1 -- 0.8)
- p3: wow/flutter depth and rate

### 3 -- Chorus
Multi-voice short-delay modulation with two detuned voices.
- p1: modulation rate (0.1 -- 6 Hz, exponential)
- p2: modulation depth (1 -- 15 ms)
- p3: feedback (0 -- 0.4)

### 4 -- Overdrive
Soft-clip `tanh` saturation with adjustable asymmetry and tone shaping.
- p1: drive gain (1x -- 20x, exponential)
- p2: tone / LPF cutoff (800 Hz -- 12 kHz, exponential)
- p3: asymmetry offset (0 -- 0.3)

### 5 -- Freezer
Spectral hold using `PV_MagFreeze` with optional blur via `PV_MagSmear`.
- p1: freeze threshold (freeze when > 0.5)
- p2: (reserved)
- p3: spectral blur amount (0 -- 12 bins)

## Parameter Table

| Parameter      | SC Arg  | Range      | Default | Description                        |
|----------------|---------|------------|---------|------------------------------------|
| algo           | algo    | 0.0 -- 5.0| 0.0     | Algorithm scan position            |
| mix            | mix     | 0.0 -- 1.0| 0.5     | Dry/wet crossfade                  |
| param1         | p1      | 0.0 -- 1.0| 0.5     | Macro 1 (algorithm-dependent)      |
| param2         | p2      | 0.0 -- 1.0| 0.5     | Macro 2 (algorithm-dependent)      |
| param3         | p3      | 0.0 -- 1.0| 0.5     | Macro 3 (algorithm-dependent)      |
| stereo_width   | width   | 0.0 -- 1.0| 0.5     | Mid-side stereo width              |
| level          | level   | 0.0 -- 1.5| 0.8     | Output VCA level                   |
| rate           | rt      | 0.3 -- 1.5| 0.8     | Source playback rate               |
| amp_attack     | attack  | 0.05 -- 2 | 0.2     | NRT envelope attack (seconds)      |
| amp_decay      | release | 0.2 -- 2  | 0.6     | NRT envelope release (seconds)     |

## Preset Continuum (Garden Activity Score)

Activity score formula:
```
activity = 0.35 * change_score_mean + 0.35 * brightness_mean + 0.3 * ndvi_mean
```

### ambient_wash (activity = 0.0) -- Sparse garden
- algo=0 (hall reverb), high mix (0.85), large room, dark damping
- Slow, spacious, ambient wash

### dub_echo (activity = 0.33) -- Moderate activity
- algo=2 (tape echo), medium mix (0.6), moderate feedback, gentle wow
- Musical, rhythmic, warm analog delays

### wide_chorus (activity = 0.67) -- Healthy garden
- algo=3 (chorus), moderate mix (0.5), medium rate/depth, wide stereo
- Lush, animated, spatially wide

### frozen_drive (activity = 1.0) -- Lush, active garden
- algo=4.8 (overdrive/freezer blend), lower mix (0.4), more dry signal
- Textural, spectral, energetic

## NRT Rendering

The module uses `Score.write` + synchronous `scsynth -N` for offline rendering. Multiple audio files from the batch are layered with staggered start times and amplitude scaling (`2.0 / sqrt(numFiles)`). Each voice gets its own FFT buffers for the freezer algorithm.

Output: 48 kHz, stereo AIFF, float format.

## Example Config JSON

```json
{
  "date": "2026-03-25",
  "module": "advanced_effects",
  "audio_batch_dir": "/path/to/audio_batches/advanced_effects",
  "output_dir": "/path/to/renders/2026-03-25",
  "duration": 120.0,
  "preset_blend": {
    "activity_score": 0.42,
    "preset_lo": "dub_echo",
    "preset_hi": "wide_chorus",
    "blend_t": 0.2647
  },
  "params": {
    "algo": 2.3529,
    "mix": 0.5735,
    "param1": 0.4368,
    "param2": 0.5368,
    "param3": 0.2368,
    "stereo_width": 0.6794,
    "level": 0.8132,
    "rate": 0.7397,
    "amp_attack": 0.1868,
    "amp_decay": 0.6235
  }
}
```
