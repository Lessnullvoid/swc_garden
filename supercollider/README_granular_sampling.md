# Granular Sampling Module -- Technical Reference

Multi-algorithm granular processor for the garden audio pipeline
(design inspired by Hologram Microcosm). Transforms field recordings
and synthetic textures into ethereal, atmospheric soundscapes shaped
by daily camera data.

Five grain algorithms -- Haze, Mosaic, Tunnel, Strum, Glide -- are
selectable via a continuous `algo` parameter and crossfaded with
`SelectX.ar`. All algorithms share the same post-processing chain
(RLPF, delay/feedback, FreeVerb2, dry/wet blend).

---

## Signal Flow

```
  Source WAV -----> [b_allocReadChannel] -----> mono buffer
   (stereo)           channel 0 only                |
                                                    v
                  +-----------------------------------------------+
                  |        Grain Engine Bank (SelectX on algo)     |
                  |                                               |
                  |  0: Haze    -- Dust triggers, slow scan       |
                  |  1: Mosaic  -- 3 TGrains at 0.5x/1x/2x       |
                  |  2: Tunnel  -- tight loop, doubled density     |
                  |  3: Strum   -- Impulse triggers, rhythmic      |
                  |  4: Glide   -- LFO pitch drift, fast scan     |
                  +-----------------------------------------------+
                                       |
                              SelectX.ar(algo)
                                       |
                                       v
               +---------------------------------------------------+
               |          Shared Post-Processing (unchanged)        |
               |                                                   |
               |   RLPF (lpfFreq, rq=0.5)                         |
               |       |                                           |
               |       v                                           |
               |   LocalIn feedback -----+                         |
               |       |                 |                         |
               |       v                 |                         |
               |   DelayC (dlTime)      |                         |
               |       |                 |                         |
               |   LocalOut  -----------+                         |
               |       |                                           |
               |       v                                           |
               |   XFade2 (dlMix)  filtered <-> delayed            |
               |       |                                           |
               |       v                                           |
               |   FreeVerb2 (room 0.85, damp 0.5, vbMix)         |
               |       |                                           |
               |       v               v                           |
               |    wetPath         dryPath (filtered grains)      |
               |       +--- dryMix blend ---+                      |
               |                |                                  |
               |                v                                  |
               |         EnvGen (ASR)                              |
               |                |                                  |
               |                v                                  |
               |         Limiter (0.95)                            |
               |                |                                  |
               |                v                                  |
               |             Out.ar                                |
               +---------------------------------------------------+
```

---

## Source Files

| Item | Path |
|------|------|
| SynthDef + NRT score | `supercollider/granular_sampling.scd` |
| Real-time SynthDef | `supercollider/explorer_server.scd` (`\gardenMicrocosm`) |
| Parameter mapping | `garden_audio/config_generator.py` (`_granular_sampling_config`) |
| Module defs (GUI) | `exploration/module_defs.py` (`GRANULAR_ALGO_INFO`) |
| Source audio | `audio_batches/granular_sampling/*.wav` (permanent, reused daily) |
| Daily config | `module_configs/YYYY-MM-DD/granular_sampling.json` |
| Output render | `renders/YYYY-MM-DD/granular_sampling.aiff` |

---

## The 5 Grain Algorithms

All algorithms use `TGrains` with `Phasor`-based position scanning and share
the same parameter set. They differ in trigger pattern, position strategy,
playback rate behavior, and grain duration scaling.

### 0 -- Haze (original behavior)

Ethereal wash of overlapping grains. `Dust.ar` triggers produce stochastic,
non-rhythmic grain onsets. `Phasor` scans slowly through the buffer with
random position jitter. Pitch varies symmetrically on a logarithmic scale
via `TExpRand`.

| Key behavior | Detail |
|-------------|--------|
| Trigger | `Dust.ar(dens)` -- random |
| Scan rate | `pRate` (slow) |
| Position jitter | full `pJitter` range |
| Pitch | `rt * TExpRand(1/(1+rtJitter), 1+rtJitter)` |
| Grain duration | `dur` (unmodified) |

### 1 -- Mosaic (multi-speed layers)

Three `TGrains` voices at 0.5x, 1x, and 2x playback rate layered together.
Creates overlapping speed textures where the same source material is heard at
different time scales simultaneously.

| Key behavior | Detail |
|-------------|--------|
| Voices | 3 independent trigger streams |
| Rates | `rt * 0.5`, `rt`, `rt * 2.0` |
| Duration scaling | 1.5x (low), 1x (mid), 0.6x (high) |
| Amplitude balance | 2.0 + 2.0 + 1.5, mixed at 0.55x |
| Position jitter | varied per voice (1x, 1.5x, 0.5x of pJitter) |

### 2 -- Tunnel (tight loop drone)

Very narrow scanning window with doubled density and minimal jitter.
Generates sustained, hypnotic drones by looping tiny portions of the source.
Near-unity pitch variation keeps the tone stable.

| Key behavior | Detail |
|-------------|--------|
| Trigger | `Dust.ar(dens * 2)` -- doubled density |
| Scan rate | `pRate * 0.08` -- nearly stationary |
| Position jitter | `pJitter * 0.15` -- minimal |
| Pitch jitter | 0.5% deviation (0.995 to 1.005) |
| Grain duration | `dur * 2.0` -- long, overlapping grains |
| Pan width | `pWidth * 0.4` -- narrow stereo |

### 3 -- Strum (rhythmic cascades)

`Impulse.ar` replaces `Dust.ar` for regular, even triggering. Creates
pointillistic rhythmic textures like cascading plucked strings. Faster
scan rate ensures the position progresses noticeably between triggers.

| Key behavior | Detail |
|-------------|--------|
| Trigger | `Impulse.ar(dens)` -- regular, rhythmic |
| Scan rate | `pRate * 1.5` -- faster progression |
| Position jitter | `pJitter * 0.3` -- tight grouping |
| Pitch jitter | reduced to `rtJitter * 0.5` |
| Grain duration | `dur * 0.7` -- shorter, more articulate |

### 4 -- Glide (pitch-shifting shimmer)

Grains with slow continuous pitch drift via `SinOsc.kr` LFO modulating rate.
Position scans faster than Haze. Creates shimmering, pitch-shifting textures
reminiscent of tape-speed modulation.

| Key behavior | Detail |
|-------------|--------|
| Trigger | `Dust.ar(dens * 0.8)` |
| Scan rate | `pRate * 1.8` -- fast exploration |
| Pitch drift | `SinOsc.kr(0.07 + rtJitter*0.3).range(0.85, 1.18)` |
| Additional jitter | `TExpRand(0.97, 1.03)` per grain |
| Grain duration | `dur * 1.3` -- slightly longer |

---

## SynthDef: `\gardenMicrocosm`

### Parameters

| SC arg | JSON key | Range | Default | Role |
|--------|----------|-------|---------|------|
| `algo` | `algo` | 0.0 -- 4.0 | 0.0 | Grain algorithm selection (crossfaded) |
| `dens` | `grain_density` | 4 -- 80 Hz | 20 | Grain trigger rate |
| `dur` | `grain_duration` | 0.02 -- 0.5 s | 0.12 | Base grain length |
| `pRate` | `pos_rate` | 0.001 -- 0.05 | 0.008 | Phasor scan speed |
| `pJitter` | `pos_jitter` | 0.0 -- 0.3 | 0.05 | Position randomization |
| `rt` | `rate` | 0.3 -- 2.0 | 0.85 | Base playback rate |
| `rtJitter` | `rate_jitter` | 0.0 -- 0.5 | 0.1 | Pitch variation depth |
| `pWidth` | `pan_width` | 0.0 -- 1.0 | 0.8 | Stereo scatter width |
| `lpfFreq` | `lpf` | 500 -- 18000 Hz | 6000 | Resonant LPF cutoff |
| `dlMix` | `delay_mix` | 0.0 -- 1.0 | 0.35 | Dry/delay crossfade |
| `dlTime` | `delay_time` | 0.01 -- 1.0 s | 0.33 | Delay line length |
| `fb` | `feedback` | 0.0 -- 0.7 | 0.4 | Delay feedback gain |
| `vbMix` | `reverb_mix` | 0.0 -- 1.0 | 0.4 | Reverb wet/dry mix |
| `drMix` | `dry_mix` | 0.0 -- 1.0 | 0.2 | Dry grain presence in final output |

### Algorithm Selection

`SelectX.ar(algo.clip(0, 4), [...])` crossfades smoothly between adjacent
algorithms. The `algo` parameter is lagged (0.5s in real-time mode) to
prevent clicks during transitions. Integer values select pure algorithms;
intermediate values blend neighbors:

- `algo = 0.0` -- pure Haze
- `algo = 0.5` -- 50% Haze + 50% Mosaic
- `algo = 1.0` -- pure Mosaic
- `algo = 2.5` -- 50% Tunnel + 50% Strum
- `algo = 4.0` -- pure Glide

### Post-Processing Chain (shared by all algorithms)

| Stage | UGen | Controls |
|-------|------|----------|
| Tone shaping | `RLPF.ar` (rq=0.5) | `lpfFreq` |
| Delay smear | `LocalIn`/`LocalOut` + `DelayC.ar` | `dlTime`, `fb` |
| Delay mix | `XFade2.ar` | `dlMix` |
| Reverb | `FreeVerb2.ar` (room=0.85, damp=0.5) | `vbMix` |
| Dry blend | `(filtered * drMix) + (wet * (1 - drMix))` | `drMix` |
| Envelope | `EnvGen.kr` (ASR) | `attack`, `sustain`, `release` |
| Limiter | `Limiter.ar` at 0.95 | (fixed) |

---

## Preset Blending System

Five curated presets are placed on an activity continuum `[0, 1]`. Each
preset is tuned for a different grain algorithm. Camera data computes a
composite activity score to select the blend position.

### Activity Score

```
activity = 0.5 * change_score_mean + 0.3 * brightness_mean + 0.2 * ndvi_mean
```

### The 5 Presets

```
  0.0        0.25         0.50         0.75         1.0
   |-----------|-----------|-----------|-----------|
 deep_haze  warm_mosaic  drone_tunnel  rhythmic_  shimmer_
  (Haze)    (Mosaic)     (Tunnel)      strum      glide
                                       (Strum)    (Glide)
```

#### deep_haze (position 0.0, algo=0) -- sparse, calm garden

Slowest scan, darkest filter, longest grains, maximum smear.
The garden was still; the sound is a deep, slow-moving fog.

| Parameter | Value |
|-----------|-------|
| algo | 0.0 (Haze) |
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

#### warm_mosaic (position 0.25, algo=1) -- gentle activity

Multi-speed layering with warm filtering. Light garden activity;
the sound is a layered, shifting tapestry of overlapping time scales.

| Parameter | Value |
|-----------|-------|
| algo | 1.0 (Mosaic) |
| grain_density | 18 Hz |
| grain_duration | 0.2 s |
| pos_rate | 0.006 |
| pos_jitter | 0.06 |
| rate | 0.85x |
| rate_jitter | 0.1 |
| pan_width | 0.85 |
| lpf | 5500 Hz |
| delay_mix | 0.38 |
| delay_time | 0.35 s |
| feedback | 0.42 |
| reverb_mix | 0.45 |
| dry_mix | 0.2 |

#### drone_tunnel (position 0.50, algo=2) -- moderate garden

Tight loop drone with deep reverb. Moderate garden activity;
the sound is a sustained, hypnotic tone hovering in space.

| Parameter | Value |
|-----------|-------|
| algo | 2.0 (Tunnel) |
| grain_density | 30 Hz |
| grain_duration | 0.18 s |
| pos_rate | 0.004 |
| pos_jitter | 0.015 |
| rate | 0.8x |
| rate_jitter | 0.02 |
| pan_width | 0.5 |
| lpf | 4500 Hz |
| delay_mix | 0.45 |
| delay_time | 0.4 s |
| feedback | 0.48 |
| reverb_mix | 0.52 |
| dry_mix | 0.12 |

#### rhythmic_strum (position 0.75, algo=3) -- active garden

Rhythmic cascading grains, brighter filter, more dry presence.
An active garden day; the sound has pulsing, articulate texture.

| Parameter | Value |
|-----------|-------|
| algo | 3.0 (Strum) |
| grain_density | 15 Hz |
| grain_duration | 0.1 s |
| pos_rate | 0.012 |
| pos_jitter | 0.04 |
| rate | 1.0x |
| rate_jitter | 0.08 |
| pan_width | 0.8 |
| lpf | 8000 Hz |
| delay_mix | 0.3 |
| delay_time | 0.25 s |
| feedback | 0.35 |
| reverb_mix | 0.35 |
| dry_mix | 0.3 |

#### shimmer_glide (position 1.0, algo=4) -- lush, vibrant garden

Pitch-shifting grains with sparkling texture, most dry signal.
A lush, sunlit garden; the sound shimmers and breathes.

| Parameter | Value |
|-----------|-------|
| algo | 4.0 (Glide) |
| grain_density | 25 Hz |
| grain_duration | 0.15 s |
| pos_rate | 0.015 |
| pos_jitter | 0.07 |
| rate | 1.05x |
| rate_jitter | 0.15 |
| pan_width | 0.75 |
| lpf | 10000 Hz |
| delay_mix | 0.28 |
| delay_time | 0.22 s |
| feedback | 0.3 |
| reverb_mix | 0.32 |
| dry_mix | 0.32 |

### Blending Logic

For a given activity score, the system finds the two nearest presets and
linearly interpolates every parameter (including `algo`) between them:

- activity = 0.0 gives pure **deep_haze** (algo 0, Haze)
- activity = 0.125 gives 50% deep_haze + 50% warm_mosaic (algo ~0.5, Haze > Mosaic)
- activity = 0.25 gives pure **warm_mosaic** (algo 1, Mosaic)
- activity = 0.375 gives 50% warm_mosaic + 50% drone_tunnel (algo ~1.5, Mosaic > Tunnel)
- activity = 0.50 gives pure **drone_tunnel** (algo 2, Tunnel)
- activity = 0.75 gives pure **rhythmic_strum** (algo 3, Strum)
- activity = 1.0 gives pure **shimmer_glide** (algo 4, Glide)

Because `algo` is included in the preset parameters, the grain algorithm
changes smoothly along the activity continuum. `SelectX.ar` handles the
crossfade between adjacent algorithms in the DSP engine.

### Design Guarantees

Every preset is hand-tuned and interpolation is linear between adjacent
presets, so every possible output is guaranteed to be:

- **Lush** -- reverb mix 0.32 to 0.52, always spacious
- **Smeared** -- delay mix 0.28 to 0.5, feedback 0.3 to 0.5
- **Textured** -- algorithm-specific grain patterns across the continuum
- **Present** -- dry mix 0.12 to 0.32, original character always audible
- **Smooth** -- LPF 3800 to 10000 Hz, high end always softened

No combination of camera data can produce harsh, empty, or ugly results.

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
the Haze algorithm captures the original ethereal grain cloud; Mosaic creates
multi-rate layers (similar to Microcosm's multi-speed looper); Tunnel produces
dense drones from small buffer segments; Strum delivers rhythmic cascades; and
Glide adds pitch-shifting shimmer. The shared post-processing chain (RLPF,
delay feedback, FreeVerb2) provides the "always beautiful" guarantee.
Adapted for offline NRT rendering driven by environmental camera data.
