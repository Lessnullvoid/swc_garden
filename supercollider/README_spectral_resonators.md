# Spectral Resonators Module -- Technical Reference

4ms SMR-inspired 6-band resonator instrument for the garden audio pipeline.
Transforms field recordings and synthetic textures into resonant, tuned
spectral structures -- from deep gong-like drones to bright crystalline
rings -- shaped by daily camera data and garden activity.

---

## Processing Chain

```
                       LAYER 1                    LAYER 2                    LAYER 3
                     Excitation               Resonator Bank             Stereo Routing

Source WAV -----> [b_allocReadChannel] -----> mono buffer
 (stereo)           channel 0 only                |
                                                  v
                  PlayBuf (variable rate)
                         |
                         v
                  + WhiteNoise * noiseAmt  (noise excitation)
                         |
                         v
                  * exciteGain
                         |
                         v
              +----------+----------+----------+----------+----------+
              |          |          |          |          |          |
           Resonz     Resonz     Resonz     Resonz     Resonz     Resonz
           f0+LFO     f1+LFO     f2+LFO     f3+LFO     f4+LFO     f5+LFO
           (rq)       (rq)       (rq)       (rq)       (rq)       (rq)
              |          |          |          |          |          |
           band 0     band 1     band 2     band 3     band 4     band 5
              |          |          |          |          |          |
              +----+     |     +---+     +---+     +---+     +----+
              |    |     |     |         |         |    |     |
              v    |     v     |         v         |    v     |
            LEFT   |   RIGHT  |       LEFT        | RIGHT    |
          (0,2,4)  |  (1,3,5) |      (0,2,4)     |(1,3,5)   |
              |    |     |    |         |         |    |      |
              +----+-----+----+---------+---------+----+------+
                         |
                         v
                      LeakDC
                         |
                    +----+----+
                    |         |
                    v         v
              FreeVerb2   dryPath
              (vbMix,       |
               vbRoom,      |
               damp 0.4)    |
                    |         |
                    v         v
                 wetPath   dryPath
                    |         |
                    +-- drMix blend --+
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
| SynthDef + NRT score | `supercollider/spectral_resonators.scd` |
| Parameter mapping | `garden_audio/config_generator.py` (`_spectral_resonators_config`) |
| Source audio | `audio_batches/spectral_resonators/*.wav` (permanent, reused daily) |
| Daily config | `module_configs/YYYY-MM-DD/spectral_resonators.json` |
| Output render | `renders/YYYY-MM-DD/spectral_resonators.aiff` |

---

## SynthDef: `\gardenSMR`

### Layer 1 -- Excitation

| UGen | Role | Controls |
|------|------|----------|
| `PlayBuf.ar` | Reads source buffer at variable speed | `rt` (playback rate) |
| `WhiteNoise.ar` | Noise excitation for self-resonance | `noiseAmt` (blend level) |
| Mix + gain | Combines audio and noise, scales amplitude | `excGain` |

The excitation layer blends the audio source with white noise before
feeding the resonator bank. Noise excitation allows the resonators to
ring even during quiet passages in the source material -- the SMR's
ability to produce sound without external input.

### Layer 2 -- Six-Band Resonator Bank

| UGen | Role | Controls |
|------|------|----------|
| 6x `Resonz.ar` | Parallel resonant bandpass filters | `f0`--`f5` (center freqs), `rq` (reciprocal Q) |
| 6x `SinOsc.kr` | Per-band slow LFO frequency drift (morph) | `mRate` (drift speed) |

Each band has its own slow LFO that gently modulates its center frequency
during the render. The LFOs have staggered rates (offset by factor 0.17
per band) and phases (offset by 60 degrees per band) so the six bands
drift independently, creating evolving harmonic movement.

The `rq` parameter (reciprocal of Q) controls the resonance character:
- rq = 0.003: very sharp ringing, struck-resonator / gong behavior
- rq = 0.015: medium resonance, chord-like spectral emphasis
- rq = 0.06: wide filter-bank, gentle coloration

### Layer 3 -- Stereo Odd/Even Routing

| UGen | Role | Controls |
|------|------|----------|
| Sum (odd) | Bands 0, 2, 4 summed to left channel | (structural) |
| Sum (even) | Bands 1, 3, 5 summed to right channel | (structural) |
| `LeakDC.ar` | Removes DC offset from resonant ringing | (fixed) |

The odd/even stereo distribution is a defining feature of the 4ms SMR.
It creates natural stereo width from the harmonic structure itself --
adjacent harmonics alternate between channels, producing a spacious
image that varies with the frequency content.

### Output Stage

| UGen | Role | Controls |
|------|------|----------|
| `FreeVerb2.ar` | Stereo reverb, damp 0.4 | `vbMix` (wet/dry), `vbRoom` (room size) |
| dry/wet blend | `(resonated * drMix) + (wet * (1 - drMix))` | `drMix` |
| `EnvGen.kr` | ASR envelope over the full render duration | `attack`, `sustain`, `release` |
| `Limiter.ar` | Brickwall limiter at 0.95 to prevent clipping from resonant buildup | (fixed) |

---

## Scale System

Band frequencies are derived from a **harmonic series** (partials 1--12)
rather than arbitrary or geometric spacing. This ensures every frequency
set is musically coherent -- all bands are natural harmonics of the root.

### Harmonic Scale

```
HARMONIC_SCALE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
```

### Frequency Resolution

Three parameters control which 6 harmonics are selected:

- **root_freq**: The fundamental frequency (40--220 Hz)
- **rotate**: Offset into the scale (0--11), shifts all 6 bands together
- **spread**: Step distance between bands (1--5)

```
band_freq[i] = root_freq * HARMONIC_SCALE[(rotate + i * spread) % 12]
```

Examples with root = 110 Hz:

| Rotate | Spread | Band Frequencies (Hz) | Character |
|--------|--------|-----------------------|-----------|
| 0 | 1 | 110, 220, 330, 440, 550, 660 | Dense harmonic cluster |
| 0 | 2 | 110, 330, 550, 770, 990, 1210 | Odd harmonics (hollow) |
| 2 | 3 | 330, 660, 990, 1320, 220, 550 | Wide harmonic spread |
| 3 | 2 | 440, 660, 880, 1100, 1320, 220 | Shifted cluster |

The wrapping behavior (modulo 12) means high spread values create
interesting non-sequential harmonic patterns.

---

## Parameter Table

| Parameter | SC arg | Range | Role |
|-----------|--------|-------|------|
| root_freq | (resolved to f0-f5) | 40--220 Hz | Fundamental frequency |
| spread | (resolved to f0-f5) | 1--5 steps | Interval between bands |
| rotate | (resolved to f0-f5) | 0--11 | Scale rotation offset |
| rq | `rq` | 0.002--0.08 | Reciprocal Q (lower = more ringing) |
| noise_mix | `noiseAmt` | 0.0--0.4 | Noise excitation blend |
| rate | `rt` | 0.4--1.3 | PlayBuf playback speed |
| excite_gain | `excGain` | 0.5--2.5 | Excitation drive level |
| morph_rate | `mRate` | 0.03--0.4 Hz | Per-band LFO drift speed |
| reverb_mix | `vbMix` | 0.15--0.55 | FreeVerb2 wet/dry |
| reverb_room | `vbRoom` | 0.4--0.93 | FreeVerb2 room size |
| dry_mix | `drMix` | 0.1--0.4 | Resonated signal preservation |
| amp_attack | `attack` | fraction of duration | Envelope attack |
| amp_decay | `release` | fraction of duration | Envelope release |

---

## Preset Blending System

Instead of mapping each parameter independently, the module uses **4 curated
presets** placed on a garden-activity continuum. Camera data computes a
single composite activity score, and the two nearest presets are smoothly
interpolated. Every preset is hand-tuned to sound beautiful on its own,
so every blend point is also beautiful.

### Activity Score

```
activity = 0.35 * change_score_mean + 0.35 * brightness_mean + 0.3 * ndvi_mean
```

All three features are in `[0, 1]` from the dataset. The formula gives
equal weight to motion and brightness (spatial/structural character)
with NDVI (vegetation richness) as a supporting factor.

### The 4 Presets

```
  0.0            0.33            0.67            1.0
   |---------------|---------------|---------------|
deep_gong      warm_filter    spectral_chord  crystalline_ring
```

#### deep_gong (position 0.0) -- sparse, dim garden

Very resonant (rq 0.003), low root (55 Hz), tight spread, slow morph.
Deep, sustained, gong-like ringing on closely spaced low harmonics.

| Parameter | Value |
|-----------|-------|
| root_freq | 55 Hz |
| spread | 1 step |
| rotate | 0 |
| rq | 0.003 |
| noise_mix | 0.3 |
| rate | 0.5x |
| excite_gain | 0.8 |
| morph_rate | 0.04 Hz |
| reverb_mix | 0.55 |
| reverb_room | 0.92 |
| dry_mix | 0.1 |

#### warm_filter (position 0.33) -- moderate activity

Wide Q (rq 0.06, filter-bank character), moderate root (110 Hz),
spread 2 for alternating harmonics, gentle coloration.

| Parameter | Value |
|-----------|-------|
| root_freq | 110 Hz |
| spread | 2 steps |
| rotate | 2 |
| rq | 0.06 |
| noise_mix | 0.12 |
| rate | 0.75x |
| excite_gain | 1.5 |
| morph_rate | 0.1 Hz |
| reverb_mix | 0.42 |
| reverb_room | 0.78 |
| dry_mix | 0.22 |

#### spectral_chord (position 0.67) -- healthy garden

Medium resonance (rq 0.015), higher root (165 Hz), wide spread 3
for a broad harmonic constellation, chord-like spectral structure.

| Parameter | Value |
|-----------|-------|
| root_freq | 165 Hz |
| spread | 3 steps |
| rotate | 4 |
| rq | 0.015 |
| noise_mix | 0.2 |
| rate | 0.9x |
| excite_gain | 1.2 |
| morph_rate | 0.2 Hz |
| reverb_mix | 0.35 |
| reverb_room | 0.65 |
| dry_mix | 0.28 |

#### crystalline_ring (position 1.0) -- lush, active garden

Sharp resonance (rq 0.005), high root (220 Hz), spread 2, bright
and sparkling marimba-like struck resonances with fast morph.

| Parameter | Value |
|-----------|-------|
| root_freq | 220 Hz |
| spread | 2 steps |
| rotate | 3 |
| rq | 0.005 |
| noise_mix | 0.25 |
| rate | 1.1x |
| excite_gain | 1.0 |
| morph_rate | 0.3 Hz |
| reverb_mix | 0.28 |
| reverb_room | 0.55 |
| dry_mix | 0.35 |

### Blending Logic

For a given activity score, the system finds the two nearest presets and
linearly interpolates every parameter between them. After blending,
`spread` and `rotate` are rounded to integers for scale lookup, and
6 band frequencies are resolved from the harmonic scale.

- activity = 0.0 gives pure **deep_gong**
- activity = 0.16 gives 50% deep_gong + 50% warm_filter
- activity = 0.33 gives pure **warm_filter**
- activity = 0.50 gives 50% warm_filter + 50% spectral_chord
- activity = 1.0 gives pure **crystalline_ring**

### Design Guarantees

Because every preset is hand-tuned and the interpolation is linear between
adjacent presets, every possible output is guaranteed to be:

- **Resonant** -- rq ranges from 0.003 (ringing) to 0.06 (filter-bank), always harmonic
- **Musical** -- frequencies are always natural harmonics of the root
- **Spatial** -- odd/even stereo routing creates natural width at all presets
- **Present** -- dry mix from 0.1 to 0.35, resonated character always audible
- **Lush** -- reverb mix from 0.28 to 0.55, always spacious

No combination of camera data can produce harsh, empty, or ugly results.

---

## Example Config

```json
{
  "date": "2026-03-25",
  "module": "spectral_resonators",
  "audio_batch_dir": "/path/to/audio_batches/spectral_resonators",
  "output_dir": "/path/to/renders/2026-03-25",
  "duration": 120.0,
  "band_freqs": [55.0, 110.0, 165.0, 220.0, 275.0, 330.0],
  "preset_blend": {
    "activity_score": 0.1230,
    "preset_lo": "deep_gong",
    "preset_hi": "warm_filter",
    "blend_t": 0.3727
  },
  "params": {
    "root_freq": 75.5,
    "spread": 1.37,
    "rotate": 0.75,
    "rq": 0.0243,
    "noise_mix": 0.2331,
    "rate": 0.593,
    "excite_gain": 1.061,
    "morph_rate": 0.0624,
    "reverb_mix": 0.5015,
    "reverb_room": 0.8678,
    "dry_mix": 0.1447,
    "amp_attack": 0.2238,
    "amp_decay": 0.6627
  }
}
```

Interpretation: activity score 0.12 places this day 37% of the way from
**deep_gong** toward **warm_filter**. The garden was calm with dim light.
After rounding, spread=1 and rotate=1, selecting adjacent harmonics starting
from the 2nd partial. Root 75.5 Hz gives a low fundamental. Very sharp
resonance (rq 0.024), slow morph (0.06 Hz), heavy reverb (room 0.87, mix 0.50).
The result is a deep, slowly drifting resonant drone.

---

## NRT Rendering

The module runs in SuperCollider's non-real-time (NRT) mode:

1. `Score.write()` serializes the OSC score to a binary `.osc` file
2. `scsynth -N` is called synchronously (blocking) via `unixCmdGetStdOut`
3. Output: stereo, 48 kHz, 32-bit float AIFF, 120 seconds (configurable)
4. One synth instance per source file, staggered by 2 seconds
5. Per-synth amplitude is `4.0 / sqrt(numFiles)` to ensure adequate volume

Per-band LFO drift works correctly in NRT mode because it runs within
the SynthDef graph using standard UGens.

---

## Key Differences from 4ms SMR

| SMR Feature | Garden Implementation | Notes |
|-------------|----------------------|-------|
| 6 resonator channels | 6x `Resonz.ar` | Same core concept |
| Scale/bank system | Harmonic series (12 partials) | Simplified to one scale |
| Rotate | Scale index offset (0--11) | Set by preset blend, not CV |
| Spread | Step interval (1--5) | Set by preset blend, not CV |
| Morph | Per-band SinOsc.kr LFO drift | Continuous in NRT, not lag-based |
| Variable Q | `rq` parameter (0.002--0.08) | Global, not per-band |
| Odd/even stereo | Bands 0,2,4 left; 1,3,5 right | Same structural concept |
| Noise excitation | WhiteNoise blend | Simpler than SMR's multi-mode excitation |
| Trigger/strike | Not implemented | NRT renders continuously, no triggers |
| Lock/detune | Not implemented | Not needed for offline batch processing |
| Envelope outputs | Not implemented | No real-time control bus needed |

---

## Ancestry

Inspired by the 4ms Spectral Multiband Resonator's approach to spectral
resonance: six parallel tuned resonators with scale-based frequency
organization, rotate/spread motion through harmonic space, variable Q
from filter-bank to ringing instrument, and odd/even stereo distribution.
Adapted for offline NRT rendering driven by environmental camera data with
a garden-activity-weighted preset blending system.
