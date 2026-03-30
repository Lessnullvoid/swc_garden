# SWC Garden Optical Sensing and Audio Processing System

## Overview

This project has two subsystems that work together:

1. **Camera application** -- a PyQt5 GUI for real-time camera control,
   radiometric calibration, broadband analysis, and vegetation index
   computation from an Allied Vision Alvium USB camera.

2. **Garden audio pipeline** -- a distributed capture network (Raspberry
   Pis) that collects images throughout the day. A Mac-side script
   calibrates and extracts features every 15 minutes, and at 07:00 the
   next morning a Python + SuperCollider pipeline processes a permanent
   library of field recordings into four audio renders shaped by the
   previous day's environmental data.

3. **Monitor application** -- a PyQt5 GUI that watches incoming Pi images
   in real time, applies calibration, extracts features, writes JSON
   events, and displays live time-series charts per camera.

4. **SC Module Explorer** -- a real-time tuning GUI that connects to
   SuperCollider over OSC, lets you audition each synthesis module with
   live parameter control, save/load presets paired with data conditions,
   and receive k-NN preset proposals based on a loaded daily dataset.

The system does not sonify the garden in real time. It constructs a daily
perceptual model where environmental changes shape the behaviour of sound
synthesis processes. The garden becomes a generator of conditions.

---

## Hardware

### Camera

- **Model**: Allied Vision Alvium 1800 U-501m NIR
- **Sensor**: ON Semiconductor AR0522 (NIR+ technology)
- **Resolution**: 2592 x 1944 (5.0 MP)
- **Spectral range**: 300 - 1100 nm (visible + near-infrared)
- **QE**: 84% at 529 nm, 30% at 850 nm
- **Interface**: USB 3.1 (USB3 Vision standard)
- **Max frame rate**: 68 fps at full resolution

### Field Layer

- 2 x Alvium 1800 U-501m NIR (near cameras: cam01, cam02)
- 1 x Arducam ToF depth camera (cam03)
- 3 x Raspberry Pi 4 (2 GB), one per camera
- WiFi network (point-to-multipoint)

### Central Node

- Mac mini (macOS, Apple Silicon)
- Local network router

### Audio Engine

- SuperCollider (offline / NRT batch processing)

---

## Project Structure

```
software/
  camera_app.py                Main PyQt5 GUI application (interactive camera control)
  monitor_app.py               Mac-side monitoring GUI (watches incoming Pi images)
  process_incoming.py          Mac: calibrate Pi images, extract features, write JSON
  requirements.txt             Python dependencies

  processing/                  Image processing library
    __init__.py                Package exports
    calibration.py             Dark frame / flat field / radiance calibration
    vegetation.py              NDVI / GNDVI / SAVI / EVI + band alignment
    analysis.py                Histogram, reflectance map, change detection
    spectral_calibration.py    Two-point and unmixing spectral radiance
    spectral_radiance.py       Wavelength-domain radiance utilities
    digital_filters.py         Bandpass filter simulation

  garden_audio/                Python package -- daily audio pipeline
    __init__.py
    dataset_builder.py         Aggregate JSON events into daily dataset
    config_generator.py        Map features to SC module parameters (preset blending)
    pipeline.py                07:00 entry point (dataset -> configs -> render -> master)
    mastering.py               LUFS loudness normalization + true-peak limiting

  exploration/                 Real-time SC tuning GUI with preset learning
    __init__.py
    explorer_gui.py            PyQt5 GUI: per-module sliders, waveform, transport
    sc_bridge.py               OSC bridge to SuperCollider (sclang subprocess)
    module_defs.py             Parameter definitions for all 4 SC modules
    preset_library.py          Save/load/delete presets paired with data features
    ml_engine.py               k-NN similarity proposals from saved presets

  supercollider/               SuperCollider modules
    run_daily.scd              NRT orchestrator -- runs all 4 modules sequentially
    explorer_server.scd        Real-time server for the Explorer GUI (OSC listener)
    granular_sampling.scd      Module 1: TGrains granular + delay + reverb
    spectral_resynthesis.scd   Module 2: FFT spectral shaping + warp + tilt EQ
    spectral_resonators.scd    Module 3: Resonz harmonic filter bank (6 bands)
    advanced_effects.scd       Module 4: multi-algorithm FX (6 algorithms)
    README_granular_sampling.md
    README_spectral_resynthesis.md
    README_spectral_resonators.md
    README_advanced_effects.md

  pi_capture/                  Raspberry Pi files (copy to each Pi)
    capture_and_send.py        Alvium: continuous capture + SCP to Mac
    capture_and_send_tof.py    Arducam ToF: depth/confidence/amplitude + SCP
    capture_calibration.py     One-time: headless dark frame / flat field capture
    live_view.py               Alvium: MJPEG HTTP stream for positioning
    live_view_tof.py           ToF: MJPEG HTTP stream (depth + amplitude)
    garden-capture.service     systemd oneshot service
    garden-capture.timer       systemd timer (every 10 min)

  calibration/                 Per-camera calibration data (created at deployment)
    cam01_dark.npy             Dark frame for cam01
    cam01_flat.npy             Flat field for cam01
    cam01_ref.npy              Change detection reference (auto-managed)

  user_presets/                Explorer GUI saved presets (per module JSON)

  launchd/                     macOS scheduling
    com.swc.garden_audio.plist LaunchAgent -- triggers pipeline at 07:00
    com.swc.process_incoming.plist LaunchAgent -- processes Pi images every 15 min

  incoming_data/               JPEG + metadata + event JSON from Pis (runtime)
  daily_datasets/              Aggregated dataset.json + pipeline logs (runtime)
  module_configs/              SC config JSONs per day (runtime)
  audio_batches/               Permanent source audio library (per module)
    granular_sampling/         Field recordings for granular module
    spectral_resynthesis/      Source material for spectral processing
    spectral_resonators/       Excitation sources for resonator bank
    advanced_effects/          Dry sources for the FX chain
  renders/                     Output AIFF files per day (runtime)
  snapshots/                   Saved images from camera_app (auto-created)
  calibration.npz              Saved calibration data
  dark_frame.npy               Dark frame reference
  flat_field.npy               Flat field reference
  venv/                        Python virtual environment
```

---

## Prerequisites

### macOS (Mac mini)

**Vimba X SDK**: Download and install from Allied Vision:
https://www.alliedvision.com/en/support/software-downloads/vimba-x-sdk/vimba-x

**Python environment** (Python 3.9+ or 3.14):

```bash
cd /Users/microhm/Desktop/01_Proyectos/SWC/software
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

VmbPy must be installed separately. The version must match the VmbC
library from your Vimba X SDK installation. The current setup uses
VmbPy 1.0.4 matching VmbC 1.0.5:

```bash
pip install ./vmbpy-1.0.4-py3-none-any.whl
```

Note: on macOS, pip may require `--trusted-host pypi.org --trusted-host
files.pythonhosted.org` if SSL verification fails.

**SuperCollider**: Install from https://supercollider.github.io or via
Homebrew (`brew install supercollider`). The `sclang` binary must be
accessible via PATH, Homebrew (`/opt/homebrew/bin/sclang`), or
`/Applications/SuperCollider.app/Contents/MacOS/sclang`.

| Package        | Version  |
|----------------|----------|
| numpy          | 2.4.2    |
| opencv-python  | 4.13.0   |
| PyQt5          | 5.15.11  |
| vmbpy          | 1.0.4    |
| pyloudnorm     | latest   |
| soundfile      | latest   |
| python-osc     | latest   |
| matplotlib     | latest   |

### Raspberry Pi (each)

- **OS**: Raspberry Pi OS 64-bit Lite (Bookworm). The 64-bit image is
  required because Vimba X ARM GenTL libraries and VmbPy need aarch64.
- **Vimba X SDK**: Linux ARM64 package with GenTL installed.
- **Environment variable**: `GENICAM_GENTL64_PATH=/opt/VimbaX/cti/arm64`
- **Python packages**: numpy, opencv-python-headless, vmbpy (no PyQt5
  needed for headless capture).
- **SSH key-based auth** to the Mac for passwordless SCP.

See `README_RASPBERRY_PI.md` for detailed Pi setup instructions.

---

## Part 1: Camera Application (camera_app.py)

### Running

Close Vimba Viewer (or any other app using the camera) first.

```bash
source venv/bin/activate
python3 camera_app.py
```

CLI options:

```bash
python3 camera_app.py --list                     # List cameras
python3 camera_app.py --camera-id "DEV_XXX"      # Use specific camera
python3 camera_app.py --low-memory                # Reduced refresh for Pi
```

### GUI Layout

```
+------------------------------------------------------+
| File  |  View: [Raw v] [x] Apply Calibration         |
+-------------------------------+----------------------+
|                               | [Camera | Calib |    |
|                               |  Analysis | Veg ]    |
|       Live View               |                      |
|       (camera feed)           |  (active tab panel)  |
|                               |                      |
+-------------------------------+----------------------+
| FPS: 68.0       |       2592x1944  Mono8  [CAL]      |
+------------------------------------------------------+
```

### Tab: Camera

| Control        | Widget             | Notes                    |
|----------------|--------------------|--------------------------|
| Exposure Auto  | Dropdown           | Off / Once / Continuous  |
| Exposure Time  | Slider + SpinBox   | Log scale, microseconds  |
| Gain Auto      | Dropdown           | Off / Once / Continuous  |
| Gain           | Slider + SpinBox   | Linear, dB               |
| Black Level    | Slider + SpinBox   |                          |
| Gamma          | Slider + SpinBox   | 0.50 - 2.50             |
| Pixel Format   | Dropdown           | Mono8 / Mono10 / Mono12 |
| Binning        | Dropdown           | 1x1 / 2x2               |

### Tab: Calibration

Radiometric calibration pipeline:

1. **Dark Frame** -- Cap the lens and capture. Averages 16 frames to
   reduce noise. Corrects sensor dark current and thermal noise.
2. **Flat Field** -- Point at a uniformly lit white surface and capture.
   Corrects lens vignetting and pixel-to-pixel sensitivity variations.
3. **Calibration Coefficient (K)** -- Scaling factor for absolute radiance.
4. **Save/Load** -- Persist calibration data as `.npz` or individual `.npy`.

Formula: `L = (DN - dark) / (flat * exposure * gain) * K`

### Tab: Analysis

Live broadband tools (no filters required):

- **Intensity Histogram** -- Real-time 256-bin histogram with mean marker
- **Image Statistics** -- Min, max, mean, std dev, median
- **Change Detection** -- Set a reference frame, then view temporal
  differences (hot colormap)

View modes (dropdown above live image):

| Mode              | Description                                    |
|-------------------|------------------------------------------------|
| Raw               | Direct camera output                           |
| Calibrated        | After dark/flat/gain/exposure normalization     |
| Reflectance Map   | False-colour intensity (TURBO colormap)        |
| Change Detection  | Difference from reference frame (HOT colormap) |

### Tab: Vegetation

Vegetation index computation from band pairs captured through bandpass
filters (e.g. 660 nm Red, 850 nm NIR):

- Capture / Load Red and NIR band images
- Align bands automatically (ORB feature-based registration)
- Compute NDVI, GNDVI, SAVI, or EVI
- View false-colour result with statistics
- Save the result as a PNG

| Index | Formula                                      | Use                          |
|-------|----------------------------------------------|------------------------------|
| NDVI  | (NIR - Red) / (NIR + Red)                    | General vegetation health    |
| GNDVI | (NIR - Green) / (NIR + Green)                | Chlorophyll concentration    |
| SAVI  | ((NIR - Red) / (NIR + Red + L)) * (1 + L)   | Reduces soil influence       |
| EVI   | G * (NIR - Red) / (NIR + C*Red + 1)         | Enhanced, reduces atmosphere |

Recommended filters: 660 nm (Red) and 850 nm (NIR) bandpass filters from
Thorlabs or Edmund Optics.

---

## Part 2: Garden Audio Pipeline

### Architecture

```
  Pi (continuous)               Mac mini (every 15 min)        Mac mini (07:00)
  ----------------              -----------------------        ----------------
  capture frame                 process_incoming.py            pipeline.py
  save JPEG + meta     --SCP--> load dark/flat calibration     dataset_builder.py
  send to Mac                   calibrate frame                 -> dataset.json
                                extract features               config_generator.py
                                write JSON event                -> module_configs/

                                                               sclang run_daily.scd
                                                                -> renders/*.aiff
                                                               mastering.py
                                                                -> LUFS normalize
```

                                monitor_app.py (optional)
                                same pipeline, live PyQt5 GUI

### Stage 1 -- Raspberry Pi capture (pi_capture/)

Each Pi runs a capture script as a long-lived process (or via systemd timer).
Camera ID is auto-detected from hostname: node1 -> cam01, node2 -> cam02, etc.

**Alvium cameras (cam01, cam02)** -- `capture_and_send.py`:

1. Opens the camera via VmbPy, seeds exposure, then sets `ExposureAuto = Continuous`
   and grabs 15 warmup frames for the auto-exposure to converge.
2. Saves the final frame as JPEG (quality 90) + a `.meta.json` sidecar containing
   `exposure_us`, `gain_db`, `pixel_format`, and `timestamp`.
3. SCPs both files to the Mac at
   `incoming_data/YYYY-MM-DD/<camera_id>/<camera_id>_HHMMSS.{jpg,meta.json}`.
4. If SCP fails, buffers locally in `~/capture_buffer/` for later recovery.
5. Dark mode: when auto-exposure exceeds a threshold (default 100 ms), the scene
   is too dark for useful data. The script stops broadcasting and probes every
   30 minutes until light returns (hysteresis between dark/light thresholds).

**Arducam ToF camera (cam03)** -- `capture_and_send_tof.py`:

1. Opens the Arducam ToF via `ArducamDepthCamera`, captures depth/confidence/amplitude.
2. Saves as `<cam>_HHMMSS.depth.npy`, `.confidence.npy`, `.amplitude.npy`,
   `.meta.json` (with `camera_type: "tof"`, `depth_mean`, `depth_std`, `depth_range`).
3. Optionally captures an RGB image via picamera2 and saves as JPEG.
4. SCPs all files to the Mac. Buffers locally on failure.

No feature extraction or calibration happens on the Pi.

### Stage 1b -- Mac-side image processing (process_incoming.py)

Runs every 15 minutes via launchd (`com.swc.process_incoming.plist`).
For each unprocessed JPEG + `.meta.json` pair (Alvium) or `.depth.npy` + `.meta.json`
pair (ToF):

**Alvium frames:**

1. Loads per-camera dark frame and flat field from `calibration/`.
2. Applies calibration: `calibrated = (frame - dark) / flat`.
3. Extracts `brightness_mean` (normalised 0-1), `ndvi_mean` (digital
   single-frame proxy), and `change_score` (mean absolute diff vs.
   previous frame, normalised 0-1).
4. Writes a JSON event alongside the image.

**ToF frames:**

1. Loads the `.depth.npy` array.
2. Computes `depth_mean`, `depth_std`, and `change_score` (depth diff vs.
   previous frame, normalised 0-1).
3. Writes a JSON event alongside the data files.

`monitor_app.py` performs the same processing but with a live PyQt5 GUI
that shows per-camera image previews, feature values, and time-series charts.
It watches `incoming_data/` via `QFileSystemWatcher` and 5-second polling.

Example event JSON:

```json
{
  "timestamp": "2026-03-24T08:10:00Z",
  "camera_id": "cam01",
  "features": {
    "brightness_mean": 0.61,
    "ndvi_mean": 0.44,
    "change_score": 0.12
  }
}
```

ToF events use `depth_mean`, `depth_std`, `change_score`:

```json
{
  "timestamp": "2026-03-24T08:10:00Z",
  "camera_id": "cam03",
  "camera_type": "tof",
  "features": {
    "depth_mean": 2.5,
    "depth_std": 0.3,
    "change_score": 0.12
  }
}
```

### Stage 2 -- Dataset building (garden_audio/dataset_builder.py)

Called by `pipeline.py`. Walks `incoming_data/YYYY-MM-DD/` and for each
camera directory loads all JSON files, then computes:

- **cam01, cam02**: per-feature summary (mean, std, min, max, linear
  trend) for brightness_mean, ndvi_mean, change_score. Splits brightness
  into morning/noon/evening thirds.
- **cam03 (ToF)**: same summary for depth_mean, depth_std, change_score.
- **Global**: brightness variance across all near-camera events,
  inter-camera brightness correlation, anomaly detection (change_score
  events exceeding 2 sigma).

Output: `daily_datasets/YYYY-MM-DD/dataset.json`.

### Stage 3 -- Config generation (garden_audio/config_generator.py)

Reads the dataset and maps aggregated features to SC parameter ranges
using a **preset-blending** system. Each module has 4 curated presets
placed on an activity continuum (0.0 - 1.0). A composite activity score
derived from camera data selects the blend position, and the two nearest
presets are smoothly interpolated. Writes one JSON config per module
into `module_configs/YYYY-MM-DD/`.

If a close match exists in `user_presets/<module>.json` (saved from the
Explorer GUI), the user preset is used instead of the algorithmic blend.
Matching uses weighted Euclidean distance on data features.

**Granular Sampling** (cam01/cam02 motion and texture):
- Activity: `0.5 * change_score + 0.3 * brightness + 0.2 * ndvi`
- Presets: deep_haze -> warm_drift -> cloud -> shimmer
- Parameters: grain_density, grain_duration, pos_rate, pos_jitter, rate,
  rate_jitter, pan_width, lpf, delay_mix, delay_time, feedback,
  reverb_mix, dry_mix, amp_attack, amp_decay

**Spectral Resynthesis** (vegetation and light):
- Activity: `0.4 * ndvi + 0.35 * brightness + 0.25 * change_score`
- Presets: spectral_drone -> warm_partials -> bright_swarm -> crystalline
- Parameters: voices_threshold, blur, warp_stretch, warp_shift, rate,
  feedback, tilt_db, glide_freq, reverb_mix, reverb_room, dry_mix,
  amp_attack, amp_decay

**Spectral Resonators** (cam01/cam02 combined):
- Activity: `0.35 * change_score + 0.35 * brightness + 0.3 * ndvi`
- Presets: deep_gong -> warm_filter -> spectral_chord -> crystalline_ring
- Band frequencies: 6 bands resolved from harmonic scale using root_freq,
  spread, and rotate parameters
- Parameters: root_freq, spread, rotate, rq, noise_mix, rate,
  excite_gain, morph_rate, reverb_mix, reverb_room, dry_mix,
  amp_attack, amp_decay

**Advanced Effects** (cam01/cam02 combined):
- Activity: `0.35 * change_score + 0.35 * brightness + 0.3 * ndvi`
- Presets: ambient_wash -> dub_echo -> wide_chorus -> frozen_drive
- Parameters: algo (0-5 selects FX algorithm), mix, param1, param2,
  param3, stereo_width, level, rate, amp_attack, amp_decay

### Stage 4 -- SuperCollider rendering (supercollider/)

`pipeline.py` invokes `sclang run_daily.scd YYYY-MM-DD /path/to/project`.
The orchestrator iterates the 4 modules, sets `~configPath` in the
interpreter environment, and calls `executeFile` for each `.scd` module.

Each module reads its JSON config, loads audio files from `audio_batch_dir`,
defines a SynthDef, builds an NRT Score, and calls `Score.recordNRT`.
All renders are stereo, 48 kHz, 32-bit float AIFF, 120 seconds by default.

### Stage 5 -- Mastering (garden_audio/mastering.py)

After all four modules render, the pipeline applies LUFS-based mastering
(ITU-R BS.1770-4) to all AIFF files in the day's render directory:

1. Measures combined integrated loudness across all four renders.
2. Computes a single gain to reach the target (-16 LUFS).
3. Applies the gain uniformly so relative volume between modules is preserved.
4. Caps at -1 dBTP true-peak ceiling to prevent clipping.
5. Writes the mastered audio back to the same files.

### SuperCollider Module Details

**1. Granular Sampling** (`gardenMicrocosm`) -- `TGrains` with `Dust`
trigger, `Phasor` scanning position with `TRand` jitter, `TExpRand`
pitch scatter, random panning. Post-grain processing: `RLPF` lowpass
filter, `DelayC` delay line with feedback, `FreeVerb2` stereo reverb,
dry/wet mix. Global ASR envelope.

**2. Spectral Resynthesis** (`gardenPanharmonium`) -- `PlayBuf` at
variable rate, FFT with `PV_MagAbove` (voice threshold), `PV_MagSmear`
(spectral blur), `PV_BinShift` (warp stretch/shift), IFFT, `RLPF` glide
filter, `BLowShelf`/`BHiShelf` tilt EQ, feedback loop, `FreeVerb2`
stereo reverb.

**3. Spectral Resonators** (`gardenSMR`) -- 6 parallel `Resonz` filters
at harmonically-related frequencies (root * scale), `SinOsc` frequency
drift for morphing, white noise excitation mixing, alternating L/R band
assignment, `FreeVerb2` reverb.

**4. Advanced Effects** (`gardenMultiFX`) -- Multi-algorithm FX module
with 6 selectable algorithms via `SelectX`: Hall Reverb (`FreeVerb2`),
Ping-Pong Delay (cross-fed `CombC`), Tape Echo (`CombC` with wow/flutter),
Chorus (modulated `DelayC`), Overdrive (tanh distortion + tone filter),
and FFT Freezer (`PV_MagFreeze` + `PV_MagSmear`). Three macro knobs
(param1-3) control each algorithm differently. Mid-side stereo width
processing, final `Limiter`.

### Explorer GUI (exploration/)

A real-time tuning interface for auditioning and refining each SC module:

```bash
source venv/bin/activate
python -m exploration.explorer_gui
```

The Explorer boots SuperCollider via `explorer_server.scd`, which defines
all 4 module SynthDefs and listens for OSC commands on port 57120. The
Python GUI (`explorer_gui.py`) sends parameter updates over OSC for
real-time control.

Features:
- Load a daily dataset to see data features and computed activity score
- Per-module parameter sliders with live OSC feedback to SC
- Waveform display with playhead tracking
- Preset library: save/load parameter sets paired with data conditions
- k-NN proposal engine: given loaded data features, suggests the closest
  saved presets by weighted Euclidean distance
- Saved presets in `user_presets/` are also used by `config_generator.py`
  during automated pipeline runs (if a close match exists, the user
  preset overrides the algorithmic blend)

---

## Running the Audio Pipeline

### 1. Place field recordings

Source audio is organised per module (not per date). Each folder feeds
one SuperCollider module and is reused every day:

```bash
cp /path/to/granular_sources/*.wav    audio_batches/granular_sampling/
cp /path/to/spectral_sources/*.wav    audio_batches/spectral_resynthesis/
cp /path/to/resonator_sources/*.wav   audio_batches/spectral_resonators/
cp /path/to/fx_sources/*.wav          audio_batches/advanced_effects/
```

### 2. Run manually

```bash
source venv/bin/activate
python -m garden_audio.pipeline --date 2026-03-24
```

This reads `incoming_data/2026-03-24/`, writes `daily_datasets/2026-03-24/dataset.json`,
generates 4 config files in `module_configs/2026-03-24/`, validates that
each `audio_batches/<module>/` folder contains source audio, invokes sclang
to render 4 AIFF files to `renders/2026-03-24/`, masters them to -16 LUFS,
and logs everything to `daily_datasets/2026-03-24/pipeline.log`.

### 3. Enable automatic daily runs

```bash
cp launchd/com.swc.garden_audio.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.swc.garden_audio.plist
```

Disable with:

```bash
launchctl unload ~/Library/LaunchAgents/com.swc.garden_audio.plist
```

### 4. Set up Raspberry Pi capture

```bash
# On each Pi:
sudo cp pi_capture/garden-capture.service /etc/systemd/system/
sudo cp pi_capture/garden-capture.timer   /etc/systemd/system/
# Edit garden-capture.service: set --camera-id and --mac-host
sudo systemctl daemon-reload
sudo systemctl enable --now garden-capture.timer
```

---

## Testing Without Hardware

Create synthetic JSON events to test the pipeline without cameras:

```bash
mkdir -p incoming_data/2026-03-24/cam01
mkdir -p incoming_data/2026-03-24/cam02
mkdir -p incoming_data/2026-03-24/cam03
```

Create files like `incoming_data/2026-03-24/cam01/cam01_081000.json`:

```json
{
  "timestamp": "2026-03-24T08:10:00Z",
  "camera_id": "cam01",
  "features": {
    "brightness_mean": 0.61,
    "ndvi_mean": 0.44,
    "change_score": 0.12
  }
}
```

And `incoming_data/2026-03-24/cam03/cam03_081000.json`:

```json
{
  "timestamp": "2026-03-24T08:10:00Z",
  "camera_id": "cam03",
  "camera_type": "tof",
  "features": {
    "depth_mean": 2.5,
    "depth_std": 0.3,
    "change_score": 0.12
  }
}
```

Place WAV files in the per-module `audio_batches/` subfolders and run:

```bash
python -m garden_audio.pipeline --date 2026-03-24
```

---

## Config JSON Format

Each SC module receives a JSON config:

```json
{
  "date": "2026-03-24",
  "module": "granular_sampling",
  "audio_batch_dir": "/absolute/path/to/audio_batches/granular_sampling/",
  "output_dir": "/absolute/path/to/renders/2026-03-24/",
  "duration": 120.0,
  "preset_blend": {
    "activity_score": 0.42,
    "preset_lo": "warm_drift",
    "preset_hi": "cloud",
    "blend_t": 0.27
  },
  "params": {
    "grain_density": 18.4,
    "grain_duration": 0.22,
    "pos_rate": 0.008,
    "pos_jitter": 0.05,
    "rate": 0.85,
    "rate_jitter": 0.1,
    "pan_width": 0.8,
    "lpf": 6000.0,
    "delay_mix": 0.35,
    "delay_time": 0.33,
    "feedback": 0.4,
    "reverb_mix": 0.4,
    "dry_mix": 0.25,
    "amp_attack": 0.18,
    "amp_decay": 0.6
  }
}
```

The `params` object varies per module. All values are computed from the
daily dataset via preset blending -- nothing is hardcoded. The
`preset_blend` section records which two presets were interpolated and
at what position.

---

## Folder Naming Conventions

Runtime folders use ISO date format YYYY-MM-DD:

```
incoming_data/2026-03-24/cam01/cam01_081000.json
daily_datasets/2026-03-24/dataset.json
module_configs/2026-03-24/granular_sampling.json
renders/2026-03-24/granular_sampling.aiff
```

Source audio folders are permanent and named by SC module:

```
audio_batches/granular_sampling/field_recording_01.wav
audio_batches/spectral_resynthesis/texture_01.aiff
audio_batches/spectral_resonators/excitation_01.wav
audio_batches/advanced_effects/dry_source_01.wav
```

---

## Design Principles

- Frame-based capture (not video) -- one frame every 10 minutes.
- Daily dataset abstraction -- the garden's state is summarised, not streamed.
- No direct sonification -- visual features shape synthesis parameters,
  they do not become audio directly.
- Permanent source library -- field recordings and synthetic textures are
  curated once per module and reused daily; only the rendered output
  varies with each day's data.
- Preset-blending config generation -- 4 curated presets per module on an
  activity continuum, interpolated by composite camera-derived scores.
- User preset override -- the Explorer GUI lets you tune parameters under
  specific data conditions and save them; the automated pipeline can
  reuse those presets when similar conditions recur.
- Modular processing -- each SC module is independent and can be developed,
  tested, or replaced separately.
- Network independence -- Pis buffer locally if the Mac is unreachable.
- Robust logging -- every pipeline run logs to a dated log file.
- Processing modules are pure numpy/OpenCV -- no Qt dependencies.
- Calibration formula: `L = (DN - dark) / (flat * exposure * gain) * K`

---

## What Is Not Yet Implemented

- **Server upload** (pipeline step 8): rendered files are stored locally
  but not uploaded anywhere. Add an SCP/rsync/S3 step to `pipeline.py`.
- **Local buffer retry on Pi**: `capture_and_send.py` buffers events
  locally when the Mac is unreachable, but does not retry sending them
  when the connection recovers.
- **ToF camera deployment**: `capture_and_send_tof.py` exists but has
  not been field-tested. The `dataset_builder.py` ToF features
  (`edge_density`, `geometry_score`) are not currently produced by
  `process_incoming.py` -- it produces `depth_mean`, `depth_std`,
  `change_score` instead.
- **Seasonal baseline**: a seasonal rolling average for NDVI / brightness
  would give more meaningful relative activity scores.
- **Audio batch management**: no automation for placing field recordings
  in `audio_batches/<module>/`. Currently manual.

---

## Additional Documentation

- `README_RASPBERRY_PI.md` -- Detailed Pi 4 setup (OS, Vimba X, VmbPy,
  low-memory mode).
- `garden_audio_system_readme.md` -- Detailed audio pipeline architecture,
  parameter mappings, and SC module internals.
- `supercollider/README_granular_sampling.md` -- Granular module details.
- `supercollider/README_spectral_resynthesis.md` -- Spectral resynthesis details.
- `supercollider/README_spectral_resonators.md` -- Spectral resonators details.
- `supercollider/README_advanced_effects.md` -- Advanced effects details.
