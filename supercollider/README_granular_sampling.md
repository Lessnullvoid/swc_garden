# Granular Sampling Module -- Technical Reference

Microcosm-inspired granular processor for the garden audio pipeline.
Transforms field recordings and synthetic textures into ethereal,
atmospheric soundscapes shaped by daily camera data.

---

## Processing Chain

```
                        LAYER 1                    LAYER 2                   LAYER 3
                     Grain Engine              Tone + Smear                  Space
                                                                                          
 Source WAV -----> [b_allocReadChannel] -----> mono buffer                                
  (stereo)           channel 0 only                |                                      
                                                   v                                      
                  Phasor (slow file scan)                                                  
                         |                                                                
                         v                                                                
                  + TRand posJitter                                                       
                         |                                                                
                         v                                                                
                      TGrains  <--- Dust.ar (density)                                     
                    (rate, dur,     TExpRand (rateJitter)                                  
                     pan, amp)      TRand (panWidth)                                      
                         |                                                                
                         v                                                                
                       RLPF  (lpfFreq, rq=0.5) ----+---- dryPath (filtered grains)       
                         |                          |                                     
                         v                          |                                     
                  LocalIn feedback  -----+          |                                     
                         |               |          |                                     
                         v               |          |                                     
                  DelayC (dlTime)        |          |                                     
                         |               |          |                                     
                  LocalOut --------------+          |                                     
                         |                          |                                     
                         v                          |                                     
                  XFade2 (dlMix)                    |                                     
                  filtered <-> delayed              |                                     
                         |                          |                                     
                         v                          |                                     
                  FreeVerb2                         |                                     
                  (room 0.85, damp 0.5)             |                                     
                         |                          |                                     
                         v                          v                                     
                      wetPath                    dryPath                                  
                         |                          |                                     
                         +------ dryMix blend ------+                                     
                                     |                                                    
                                     v                                                    
                              EnvGen (ASR)                                                
                                     |                                                    
                                     v                                                    
                              Limiter (0.95)                                              
                                     |                                                    
                                     v                                                    
                                  Out.ar                                                  
```

---

## Source Files

| Item | Path |
|------|------|
| SynthDef + NRT score | `supercollider/granular_sampling.scd` |
| Parameter mapping | `garden_audio/config_generator.py` (`_granular_sampling_config`) |
| Source audio | `audio_batches/granular_sampling/*.wav` (permanent, reused daily) |
| Daily config | `module_configs/YYYY-MM-DD/granular_sampling.json` |
| Output render | `renders/YYYY-MM-DD/granular_sampling.aiff` |

---

## SynthDef: `\gardenMicrocosm`

### Layer 1 -- Grain Engine

| UGen | Role | Controls |
|------|------|----------|
| `Phasor.ar` | Slow steady scan through the buffer | `pRate` (scan speed) |
| `TRand.ar` | Per-grain position jitter around the pointer | `pJitter` |
| `Dust.ar` | Stochastic grain trigger stream | `dens` |
| `TExpRand.ar` | Per-grain pitch variation (exponential, more musical than linear) | `rtJitter` |
| `TGrains.ar` | Grain playback with 2-channel output, 4-point interpolation | `dur`, `rt`, `pWidth` |

`TGrains` reads from a **mono** buffer (channel 0 extracted via `b_allocReadChannel`).
The `Phasor` wraps continuously through the file at a rate controlled by `pRate`.
Each grain's position is the pointer plus a random offset in `[-pJitter, +pJitter]`,
wrapped to `[0, 1]` and scaled to buffer duration in seconds.

Grain pitch is `rt * TExpRand(1/(1+rtJitter), 1+rtJitter)`, which preserves the
center pitch while scattering symmetrically on a logarithmic scale.

### Layer 2 -- Tone + Smear

| UGen | Role | Controls |
|------|------|----------|
| `RLPF.ar` | Resonant low-pass filter, softens high end | `lpfFreq` (cutoff), rq fixed at 0.5 |
| `LocalIn`/`LocalOut` | Single-block feedback loop | (internal) |
| `DelayC.ar` | Delay line inside the feedback loop | `dlTime` |
| `XFade2.ar` | Crossfade between filtered signal and delayed signal | `dlMix` |

The feedback loop creates temporal smearing: grains echo and blur into
each other. `fb` (feedback gain) controls how much of the delayed signal
recirculates. The `XFade2` blends from pure filtered grains (`dlMix=0`)
to fully delayed/smeared (`dlMix=1`).

### Layer 3 -- Space

| UGen | Role | Controls |
|------|------|----------|
| `FreeVerb2.ar` | Stereo reverb, room 0.85, damp 0.5 | `vbMix` (wet/dry) |

Room size and damping are fixed at values that always produce a lush,
spacious reverb. Only the wet/dry mix varies with camera data.

### Output Stage

| UGen | Role | Controls |
|------|------|----------|
| dry/wet blend | `(filtered * drMix) + (wet * (1 - drMix))` | `drMix` |
| `EnvGen.kr` | ASR envelope over the full render duration | `attack`, `sustain`, `release` |
| `Limiter.ar` | Brickwall limiter at 0.95 to prevent clipping from feedback buildup | (fixed) |

---

## Preset Blending System

Instead of mapping each parameter independently, the module uses **4 curated
presets** placed on an activity continuum. Camera data computes a single
composite activity score, and the two nearest presets are smoothly
interpolated. Every preset is hand-tuned to sound beautiful on its own,
so every blend point is also beautiful.

### Activity Score

```
activity = 0.5 * change_score_mean + 0.3 * brightness_mean + 0.2 * ndvi_mean
```

All three features are in `[0, 1]` from the dataset. The weighted sum stays
in `[0, 1]` and represents overall garden activity for the day.

### The 4 Presets

```
  0.0          0.33          0.67          1.0
   |-------------|-------------|-------------|
 deep_haze    warm_drift      cloud       shimmer
```

#### deep_haze (position 0.0) -- calm, dim day

Slowest scan, darkest filter, longest grains, maximum smear.
The garden was still; the sound is a deep, slow-moving fog.

| Parameter | Value |
|-----------|-------|
| grain_density | 12 Hz |
| grain_duration | 0.25 s |
| pos_rate | 0.003 |
| pos_jitter | 0.02 |
| rate | 0.7x |
| rate_jitter | 0.05 |
| pan_width | 0.9 |
| lpf | 3800 Hz |
| delay_mix | 0.5 |
| delay_time | 0.45 s |
| feedback | 0.5 |
| reverb_mix | 0.5 |
| dry_mix | 0.15 |

#### warm_drift (position 0.33) -- gentle movement

Moderate pace, warm filter, balanced effects. Light activity in
the garden; the sound is a slow, warm current with soft grain edges.

| Parameter | Value |
|-----------|-------|
| grain_density | 20 Hz |
| grain_duration | 0.18 s |
| pos_rate | 0.008 |
| pos_jitter | 0.05 |
| rate | 0.85x |
| rate_jitter | 0.1 |
| pan_width | 0.8 |
| lpf | 6000 Hz |
| delay_mix | 0.35 |
| delay_time | 0.33 s |
| feedback | 0.4 |
| reverb_mix | 0.4 |
| dry_mix | 0.25 |

#### cloud (position 0.67) -- busy garden, still ethereal

Dense grain shower, wide stereo, heavy reverb, medium-slow scan.
Lots happening in the garden; the sound is a thick, enveloping cloud.

| Parameter | Value |
|-----------|-------|
| grain_density | 42 Hz |
| grain_duration | 0.14 s |
| pos_rate | 0.006 |
| pos_jitter | 0.1 |
| rate | 0.9x |
| rate_jitter | 0.2 |
| pan_width | 0.9 |
| lpf | 7000 Hz |
| delay_mix | 0.4 |
| delay_time | 0.38 s |
| feedback | 0.45 |
| reverb_mix | 0.48 |
| dry_mix | 0.18 |

#### shimmer (position 1.0) -- active, bright day

Fastest scan, brightest filter, shortest grains, more dry presence.
A vivid, sunlit garden day; the sound sparkles and breathes.

| Parameter | Value |
|-----------|-------|
| grain_density | 35 Hz |
| grain_duration | 0.09 s |
| pos_rate | 0.022 |
| pos_jitter | 0.08 |
| rate | 1.1x |
| rate_jitter | 0.18 |
| pan_width | 0.7 |
| lpf | 10000 Hz |
| delay_mix | 0.25 |
| delay_time | 0.22 s |
| feedback | 0.3 |
| reverb_mix | 0.3 |
| dry_mix | 0.35 |

### Blending Logic

For a given activity score, the system finds the two nearest presets and
linearly interpolates every parameter between them. For example:

- activity = 0.0 gives pure **deep_haze**
- activity = 0.16 gives 50% deep_haze + 50% warm_drift
- activity = 0.33 gives pure **warm_drift**
- activity = 0.50 gives 50% warm_drift + 50% cloud
- activity = 1.0 gives pure **shimmer**

### Design Guarantees

Because every preset is hand-tuned and the interpolation is linear between
adjacent presets, every possible output is guaranteed to be:

- **Lush** -- reverb mix ranges from 0.3 (shimmer) to 0.5 (deep_haze)
- **Smeared** -- delay mix ranges from 0.25 to 0.5, feedback from 0.3 to 0.5
- **Textured** -- grain density from 12 to 42, duration from 0.09 to 0.25 s
- **Present** -- dry mix from 0.15 to 0.35, original grain character always audible
- **Smooth** -- LPF from 3800 to 10000 Hz, always softening the high end

No combination of camera data can produce harsh, empty, or ugly results.

---

## Example Config (2026-03-25, 282 events)

```json
{
  "preset_blend": {
    "activity_score": 0.1345,
    "preset_lo": "deep_haze",
    "preset_hi": "warm_drift",
    "blend_t": 0.4075
  },
  "params": {
    "grain_density": 15.26,
    "grain_duration": 0.2215,
    "pos_rate": 0.005,
    "pos_jitter": 0.0322,
    "rate": 0.7611,
    "rate_jitter": 0.0704,
    "pan_width": 0.8592,
    "lpf": 4696.6,
    "delay_mix": 0.4389,
    "delay_time": 0.4011,
    "feedback": 0.4592,
    "reverb_mix": 0.4592,
    "dry_mix": 0.1908,
    "amp_attack": 0.2215,
    "amp_decay": 0.6592
  }
}
```

Interpretation: activity score 0.13 places this day 41% of the way from
**deep_haze** toward **warm_drift**. The garden was calm with dim light
(brightness ~0.19, change_score ~0.06, NDVI ~0.31). The result is close to
deep_haze but slightly warmer: slow-scanning (0.005), dark-filtered
(4.7 kHz), pitch-lowered (0.76x), heavy smear (delay 0.44, feedback 0.46),
and deep reverb (0.46). Dry mix at 0.19 preserves a hint of grain clarity.

---

## NRT Rendering

The module runs in SuperCollider's non-real-time (NRT) mode:

1. `Score.write()` serializes the OSC score to a binary `.osc` file
2. `scsynth -N` is called synchronously (blocking) via `unixCmdGetStdOut`
3. Output: stereo, 48 kHz, 32-bit float AIFF, 120 seconds (configurable)
4. One synth instance per source file, staggered by 2 seconds
5. Per-synth amplitude is `2.0 / sqrt(numFiles)` to balance multi-file layering

`LocalIn`/`LocalOut` feedback works correctly in NRT mode because it
operates within a single SynthDef graph (per-block feedback, not inter-synth).

---

## Ancestry

Inspired by the Hologram Microcosm pedal's approach to granular processing:
TGrains for grain generation, Phasor for file scanning, feedback delay for
temporal diffusion, and reverb as part of the texture (not just an end effect).
Adapted for offline NRT rendering driven by environmental camera data.
