# Spectral Resynthesis Module -- Technical Reference

Panharmonium-inspired spectral processor for the garden audio pipeline.
Transforms field recordings and synthetic textures into evolving spectral
soundscapes -- from sustained organ-like drones to shimmering crystalline
harmonics -- shaped by daily camera data and vegetation richness.

---

## Processing Chain

```
                       LAYER 1                    LAYER 2                    LAYER 3
                    FFT Analysis              Spectral Shaping             Tone + Glide

Source WAV -----> [b_allocReadChannel] -----> mono buffer
 (stereo)           channel 0 only                |
                                                  v
                  PlayBuf (variable rate)
                         |
                         v
                  + LocalIn feedback (fb * previous processed)
                         |
                         v
                    FFT (2048)
                         |
                         v
                  PV_MagAbove (voicesThr)    -- voice limiting
                         |
                         v
                  PV_MagSmear (blurAmt)      -- spectral blur / lag
                         |
                         v
                  PV_BinShift (wStretch,     -- spectral warp
                               wShift)
                         |
                         v
                       IFFT
                         |
                         v
                    RLPF (glFreq, rq=0.6) -------- dryPath (processed spectral signal)
                         |                                |
                         v                                |
                  BLowShelf (300 Hz, tiltDb * -0.5)       |
                  BHiShelf (3000 Hz, tiltDb * 0.5)        |
                         |                                |
                         v                                |
                  LocalOut (Limiter 0.7)                  |
                  (feedback send)                         |
                         |                                |
                         v                                |
                  Pan2 --> FreeVerb2                       |
                  (mix: vbMix, room: vbRoom,              |
                   damp: 0.45)                            |
                         |                                |
                         v                                v
                      wetPath                          dryPath
                         |                                |
                         +------- dryMix blend -----------+
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
| SynthDef + NRT score | `supercollider/spectral_resynthesis.scd` |
| Parameter mapping | `garden_audio/config_generator.py` (`_spectral_resynthesis_config`) |
| Source audio | `audio_batches/spectral_resynthesis/*.wav` (permanent, reused daily) |
| Daily config | `module_configs/YYYY-MM-DD/spectral_resynthesis.json` |
| Output render | `renders/YYYY-MM-DD/spectral_resynthesis.aiff` |

---

## SynthDef: `\gardenPanharmonium`

### Layer 1 -- FFT Analysis

| UGen | Role | Controls |
|------|------|----------|
| `PlayBuf.ar` | Reads source buffer at variable speed | `rt` (playback rate) |
| `LocalIn.ar` | Receives feedback from the processed signal | `fb` (feedback amount, capped 0--0.5) |
| `FFT` | 2048-point FFT analysis of source + feedback mix | (fixed frame size) |

The source signal is mixed with a scaled version of the previous frame's
processed output before entering the FFT. This spectral feedback creates
self-reinforcing harmonic structures that evolve over time -- the
Panharmonium's signature "freeze" and "sustain" behavior in attenuated form.

### Layer 2 -- Spectral Shaping

| UGen | Role | Controls |
|------|------|----------|
| `PV_MagAbove` | Keeps only spectral peaks above a threshold; acts as voice limiting | `voicesThr` (higher = fewer surviving peaks) |
| `PV_MagSmear` | Spreads energy across neighboring bins; creates spectral blur / lag | `blurAmt` (number of bins to smear across) |
| `PV_BinShift` | Stretches and shifts the spectrum; produces inharmonic / swarming textures | `wStretch`, `wShift` |

`PV_MagAbove` is the spectral equivalent of Panharmonium's voice count:
a high threshold retains only the loudest partials (few "voices"), while
a low threshold lets the full spectrum through. `PV_MagSmear` simulates
the spectral lag / blur that sustains and smears partials across time.
`PV_BinShift` warps the harmonic structure -- stretch factors above 1.0
spread partials outward, and shift offsets transpose the entire spectrum.

### Layer 3 -- Tone + Glide

| UGen | Role | Controls |
|------|------|----------|
| `RLPF.ar` | Resonant low-pass filter for glide smoothing | `glFreq` (cutoff), rq fixed at 0.6 |
| `BLowShelf.ar` | Low-frequency tilt EQ | `tiltDb` (negative = darker) |
| `BHiShelf.ar` | High-frequency tilt EQ | `tiltDb` (positive = brighter) |

The RLPF smooths temporal transitions between successive FFT frames,
simulating Panharmonium's glide control. Lower cutoff frequencies create
a more languid, organ-like sustain; higher cutoffs preserve transient detail.
The tilt EQ applies a complementary low/high shelf curve controlled by a
single parameter -- negative values darken the sound, positive values brighten it.

### Feedback Path

| UGen | Role | Controls |
|------|------|----------|
| `Limiter.ar` | Prevents feedback runaway (ceiling 0.7) | (fixed) |
| `LocalOut.ar` | Sends limited processed signal back to Layer 1 | (internal) |

The feedback path goes from the tilt EQ output through a limiter before
recirculating into the FFT input. The limiter at 0.7 ensures feedback
never exceeds a safe level regardless of the `fb` parameter setting.

### Output Stage

| UGen | Role | Controls |
|------|------|----------|
| `FreeVerb2.ar` | Stereo reverb, damp 0.45 | `vbMix` (wet/dry), `vbRoom` (room size) |
| dry/wet blend | `(processed * drMix) + (wet * (1 - drMix))` | `drMix` |
| `EnvGen.kr` | ASR envelope over the full render duration | `attack`, `sustain`, `release` |
| `Limiter.ar` | Brickwall limiter at 0.95 to prevent clipping | (fixed) |

---

## Parameter Table

| Parameter | SC arg | Range | Role |
|-----------|--------|-------|------|
| voices_threshold | `voicesThr` | 0.001--0.1 | PV_MagAbove threshold (lower = more voices) |
| blur | `blurAmt` | 1--48 | PV_MagSmear bin count |
| warp_stretch | `wStretch` | 0.8--1.5 | PV_BinShift stretch factor |
| warp_shift | `wShift` | -4--4 | PV_BinShift bin shift |
| rate | `rt` | 0.3--1.5 | PlayBuf playback speed |
| feedback | `fb` | 0.0--0.4 | Feedback amount (capped at 0.5 in SynthDef) |
| tilt_db | `tiltDb` | -6--6 | Spectral tilt (neg = dark, pos = bright) |
| glide_freq | `glFreq` | 2000--16000 | RLPF cutoff (lower = more smoothing) |
| reverb_mix | `vbMix` | 0.2--0.6 | FreeVerb2 wet/dry |
| reverb_room | `vbRoom` | 0.5--0.95 | FreeVerb2 room size |
| dry_mix | `drMix` | 0.1--0.4 | Original processed signal preservation |
| amp_attack | `attack` | fraction of duration | Envelope attack |
| amp_decay | `decay` | fraction of duration | Envelope release |

---

## Preset Blending System

Instead of mapping each parameter independently, the module uses **4 curated
presets** placed on a vegetation-richness continuum. Camera data computes a
single composite activity score, and the two nearest presets are smoothly
interpolated. Every preset is hand-tuned to sound beautiful on its own,
so every blend point is also beautiful.

### Activity Score

```
activity = 0.4 * ndvi_mean + 0.35 * brightness_mean + 0.25 * change_score_mean
```

The formula is weighted toward NDVI (vegetation index) since this module is
about spectral richness -- more vegetation means richer harmonic content.
All three features are in `[0, 1]` from the dataset.

### The 4 Presets

```
  0.0           0.33           0.67           1.0
   |--------------|--------------|--------------|
spectral_drone  warm_partials  bright_swarm  crystalline
```

#### spectral_drone (position 0.0) -- low vegetation, sparse garden

Few voices (high threshold), heavy blur, slow rate, dark tilt, maximum reverb.
Deep, sustained, slowly evolving organ-like tones.

| Parameter | Value |
|-----------|-------|
| voices_threshold | 0.08 |
| blur | 42 bins |
| warp_stretch | 1.0x |
| warp_shift | 0 bins |
| rate | 0.4x |
| feedback | 0.3 |
| tilt_db | -5.0 dB |
| glide_freq | 2500 Hz |
| reverb_mix | 0.55 |
| reverb_room | 0.93 |
| dry_mix | 0.12 |

#### warm_partials (position 0.33) -- moderate vegetation

More voices, moderate blur, warm tilt, balanced effects, some glide.
Musical, harmonic, gentle spectral movement.

| Parameter | Value |
|-----------|-------|
| voices_threshold | 0.04 |
| blur | 24 bins |
| warp_stretch | 1.05x |
| warp_shift | 0.5 bins |
| rate | 0.65x |
| feedback | 0.2 |
| tilt_db | -1.5 dB |
| glide_freq | 5500 Hz |
| reverb_mix | 0.42 |
| reverb_room | 0.82 |
| dry_mix | 0.22 |

#### bright_swarm (position 0.67) -- healthy garden

Many voices, less blur, some warp, brighter tilt, moderate reverb.
Rich, shimmering, spectrally complex.

| Parameter | Value |
|-----------|-------|
| voices_threshold | 0.015 |
| blur | 10 bins |
| warp_stretch | 1.15x |
| warp_shift | 1.5 bins |
| rate | 0.95x |
| feedback | 0.12 |
| tilt_db | 2.5 dB |
| glide_freq | 10000 Hz |
| reverb_mix | 0.32 |
| reverb_room | 0.7 |
| dry_mix | 0.3 |

#### crystalline (position 1.0) -- lush, active garden

Maximum voices (low threshold), minimal blur, bright tilt, more dry, slight warp.
Detailed, transparent, sparkling harmonic detail.

| Parameter | Value |
|-----------|-------|
| voices_threshold | 0.003 |
| blur | 3 bins |
| warp_stretch | 1.08x |
| warp_shift | 2.0 bins |
| rate | 1.2x |
| feedback | 0.05 |
| tilt_db | 5.0 dB |
| glide_freq | 14000 Hz |
| reverb_mix | 0.25 |
| reverb_room | 0.58 |
| dry_mix | 0.38 |

### Blending Logic

For a given activity score, the system finds the two nearest presets and
linearly interpolates every parameter between them. For example:

- activity = 0.0 gives pure **spectral_drone**
- activity = 0.16 gives 50% spectral_drone + 50% warm_partials
- activity = 0.33 gives pure **warm_partials**
- activity = 0.50 gives 50% warm_partials + 50% bright_swarm
- activity = 1.0 gives pure **crystalline**

### Design Guarantees

Because every preset is hand-tuned and the interpolation is linear between
adjacent presets, every possible output is guaranteed to be:

- **Lush** -- reverb mix ranges from 0.25 (crystalline) to 0.55 (spectral_drone)
- **Spectrally rich** -- blur ranges from 3 to 42 bins, always smoothing the spectrum
- **Evolving** -- feedback from 0.05 to 0.3, always adding self-reinforcing harmonics
- **Present** -- dry mix from 0.12 to 0.38, processed spectral character always audible
- **Smooth** -- glide RLPF from 2500 to 14000 Hz, always softening frame transitions

No combination of camera data can produce harsh, empty, or ugly results.

---

## Example Config

```json
{
  "date": "2026-03-25",
  "module": "spectral_resynthesis",
  "audio_batch_dir": "/path/to/audio_batches/spectral_resynthesis",
  "output_dir": "/path/to/renders/2026-03-25",
  "duration": 120.0,
  "preset_blend": {
    "activity_score": 0.2180,
    "preset_lo": "spectral_drone",
    "preset_hi": "warm_partials",
    "blend_t": 0.6606
  },
  "params": {
    "voices_threshold": 0.0536,
    "blur": 30.11,
    "warp_stretch": 1.033,
    "warp_shift": 0.3303,
    "rate": 0.565,
    "feedback": 0.2339,
    "tilt_db": -2.6879,
    "glide_freq": 4481.8,
    "reverb_mix": 0.4641,
    "reverb_room": 0.8573,
    "dry_mix": 0.1861,
    "amp_attack": 0.2042,
    "amp_decay": 0.6339
  }
}
```

Interpretation: activity score 0.22 places this day 66% of the way from
**spectral_drone** toward **warm_partials**. The garden had moderate
vegetation (NDVI ~0.31) with dim light (brightness ~0.19) and low
change (change_score ~0.06). The result leans warm: moderate blur (30 bins),
dark tilt (-2.7 dB), slow rate (0.57x), significant feedback (0.23),
deep reverb (room 0.86, mix 0.46), and low dry mix (0.19). The output
is a rich, slowly evolving harmonic wash with organ-like sustain.

---

## NRT Rendering

The module runs in SuperCollider's non-real-time (NRT) mode:

1. `Score.write()` serializes the OSC score to a binary `.osc` file
2. `scsynth -N` is called synchronously (blocking) via `unixCmdGetStdOut`
3. Output: stereo, 48 kHz, 32-bit float AIFF, 120 seconds (configurable)
4. One synth instance per source file, staggered by 2 seconds
5. Per-synth amplitude is `3.0 / sqrt(numFiles)` to ensure adequate volume

`LocalIn`/`LocalOut` feedback works correctly in NRT mode because it
operates within a single SynthDef graph (per-block feedback, not inter-synth).

---

## Key Differences from Panharmonium

| Panharmonium Feature | Garden Implementation | Notes |
|---------------------|----------------------|-------|
| Voice count knob | `PV_MagAbove` threshold | Continuous, not discrete voice count |
| Freeze button | Feedback loop (`LocalIn`/`LocalOut`) | Attenuated feedback (max 0.4), not full freeze |
| Blur slider | `PV_MagSmear` bin count | Same concept, 1--48 bins |
| Warp / spectral shift | `PV_BinShift` stretch + shift | Combined stretch and shift |
| Glide | RLPF on IFFT output | Temporal smoothing of spectral frames |
| Tilt | BLowShelf + BHiShelf | Single-parameter complementary shelving |
| Reverb | FreeVerb2 | Simpler than convolution reverb; adequate for NRT |

---

## Ancestry

Inspired by the Meris/Chase Bliss Panharmonium's approach to spectral
processing: FFT analysis with voice limiting for partial selection, spectral
blur for sustained harmonic washes, spectral warp for inharmonic textures,
and feedback for self-reinforcing evolving tones. Adapted for offline NRT
rendering driven by environmental camera data with a vegetation-weighted
preset blending system.
