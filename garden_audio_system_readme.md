# Garden Optical Sensing -- Audio Processing System

## Overview

This system captures environmental data from a distributed garden sensing
network (cameras on Raspberry Pis) and translates it into daily audio
renders using SuperCollider. It is a non-real-time observational system:
images are collected during daylight hours, a Mac-side script calibrates
and extracts features every 15 minutes, and the following morning (07:00)
a Python + SuperCollider pipeline processes a permanent library of field
recordings into four audio renders shaped by the previous day's data.

The garden does not produce events that are sonified directly. Instead, the
daily accumulation of brightness, vegetation indices, depth, and change
patterns shapes the behaviour of four synthesis modules that process
curated field recordings and synthetic textures into new audio files
each morning.

---

## System Components

### Field Layer (Raspberry Pis)

- 2 x Allied Vision Alvium 1800 U-501m NIR (near cameras, cam01 + cam02)
- 1 x Arducam ToF depth camera (cam03)
- Each Pi captures 1 frame every 10 minutes, saves JPEG + metadata
  sidecar, and SCPs the files to the Mac mini over WiFi.
- No feature extraction happens on the Pi.

### Central Node (Mac mini)

- Receives JPEG + `.meta.json` files from Pis via SCP.
- `process_incoming.py` runs every 15 minutes (via launchd): calibrates
  frames (dark/flat correction), extracts features (brightness, NDVI
  proxy, change score), and writes JSON event files alongside the images.
- `monitor_app.py` (optional GUI) does the same processing in real time
  with live image previews and time-series charts.
- At 07:00 runs the Python pipeline that aggregates the previous day's
  data, maps features to synthesis parameters, invokes SuperCollider,
  and masters the output.

### Audio Engine (SuperCollider)

- 4 NRT (non-real-time) modules, each with its own permanent source audio folder.
- Produces 4 AIFF files per day (one per module), 48 kHz / 32-bit float.

---

## Current Codebase

```
software/
  garden_audio/              Python package -- daily pipeline
    __init__.py
    dataset_builder.py       Aggregate JSON events into daily dataset
    config_generator.py      Map features to SC params (preset blending)
    pipeline.py              07:00 entry point (dataset -> configs -> render -> master)
    mastering.py             LUFS loudness normalization + true-peak limiting

  exploration/               Real-time SC tuning GUI with preset learning
    __init__.py
    explorer_gui.py          PyQt5 GUI: per-module sliders, waveform, transport
    sc_bridge.py             OSC bridge to SuperCollider (sclang subprocess)
    module_defs.py           Parameter definitions for all 4 SC modules
    preset_library.py        Save/load/delete presets paired with data features
    ml_engine.py             k-NN similarity proposals from saved presets

  supercollider/             SuperCollider modules
    run_daily.scd            NRT orchestrator -- runs all 4 modules sequentially
    explorer_server.scd      Real-time server for the Explorer GUI (OSC)
    granular_sampling.scd    Module 1: TGrains granular + delay + reverb
    spectral_resynthesis.scd Module 2: FFT spectral shaping + warp + tilt EQ
    spectral_resonators.scd  Module 3: Resonz harmonic filter bank (6 bands)
    advanced_effects.scd     Module 4: multi-algorithm FX (6 algorithms)

  pi_capture/                Raspberry Pi headless capture
    capture_and_send.py      Alvium: continuous capture + SCP to Mac
    capture_and_send_tof.py  Arducam ToF: depth/confidence/amplitude + SCP
    capture_calibration.py   One-time: headless dark frame / flat field
    live_view.py             Alvium: MJPEG HTTP stream for positioning
    live_view_tof.py         ToF: MJPEG HTTP stream (depth + amplitude)
    garden-capture.service   systemd oneshot service
    garden-capture.timer     systemd timer (every 10 min)

  launchd/                   macOS scheduling
    com.swc.garden_audio.plist   LaunchAgent -- triggers pipeline at 07:00
    com.swc.process_incoming.plist LaunchAgent -- calibrate + extract every 15 min

  processing/                Image processing library (used by camera_app)
    calibration.py           Dark frame / flat field calibration
    vegetation.py            NDVI / SAVI / EVI computation
    analysis.py              Change detection, histogram, statistics
    spectral_calibration.py  Two-point and unmixing spectral radiance
    spectral_radiance.py     Wavelength-domain radiance utilities
    digital_filters.py       Bandpass filter simulation

  camera_app.py              PyQt5 GUI for interactive camera control
  monitor_app.py             PyQt5 GUI for monitoring incoming Pi data
  process_incoming.py        Headless: calibrate Pi images, extract features
  requirements.txt           Python dependencies

  user_presets/              Explorer GUI saved presets (per module JSON)
  incoming_data/             Per-event JPEG + metadata from Pis (runtime)
  daily_datasets/            Aggregated dataset.json per day (runtime)
  module_configs/            SC config JSONs per day (runtime)
  audio_batches/             Permanent source audio library (per module)
    granular_sampling/       Field recordings + textures for granular module
    spectral_resynthesis/    Source material for FFT/spectral processing
    spectral_resonators/     Excitation sources for resonator bank
    advanced_effects/        Dry sources for the FX chain
  renders/                   Output AIFF files per day (runtime)
```

---

## Data Flow

```
  Pi (continuous)               Mac mini (every 15 min)         Mac mini (07:00)
  ----------------              -----------------------         ----------------
  capture frame                 process_incoming.py             pipeline.py
  save JPEG + meta    --SCP-->  load dark/flat calibration       |
  send to Mac                   calibrate frame                  dataset_builder.py
                                extract features                  -> dataset.json
                                write JSON event                 config_generator.py
                                                                  -> module_configs/
                                monitor_app.py (optional)
                                same pipeline, live GUI          sclang run_daily.scd
                                                                  -> renders/*.aiff
                                                                 mastering.py
                                                                  -> LUFS normalize
```

### Stage 1 -- Raspberry Pi capture (pi_capture/)

Each Pi runs a capture script as a long-lived process.

**Alvium cameras (cam01, cam02)** -- `capture_and_send.py`:

1. Opens the camera via VmbPy, seeds exposure to 10 ms, then sets
   `ExposureAuto = Continuous` and grabs 15 warmup frames.
2. Saves the final frame as JPEG (quality 90) + a `.meta.json` sidecar
   containing `exposure_us`, `gain_db`, `pixel_format`, and `timestamp`.
3. SCPs both files to the Mac at
   `incoming_data/YYYY-MM-DD/<camera_id>/<camera_id>_HHMMSS.{jpg,meta.json}`.
4. If SCP fails, buffers locally in `~/capture_buffer/` for later retry.
5. Dark mode: when auto-exposure exceeds a threshold (default 100 ms) or
   mean pixel value falls below 15, the scene is too dark. The script
   stops broadcasting and probes every 30 minutes until light returns.

**Arducam ToF (cam03)** -- `capture_and_send_tof.py`:

1. Opens the ToF camera via `ArducamDepthCamera`, captures depth,
   confidence, and amplitude arrays.
2. Saves as `.depth.npy`, `.confidence.npy`, `.amplitude.npy`, and
   `.meta.json` (with `camera_type: "tof"`, `depth_mean`, `depth_std`).
3. Optionally captures an RGB image via picamera2 and saves as JPEG.
4. SCPs all files to the Mac. Buffers locally on failure.

No feature extraction or calibration happens on the Pi.

### Stage 1b -- Mac-side processing (process_incoming.py / monitor_app.py)

`process_incoming.py` runs every 15 minutes via launchd. For each
unprocessed JPEG + `.meta.json` pair:

1. Loads per-camera dark frame and flat field from `calibration/`.
2. Applies calibration: `calibrated = (frame - dark) / flat`.
3. Extracts `brightness_mean` (normalised 0-1), `ndvi_mean` (digital
   single-frame proxy), and `change_score` (mean absolute diff vs.
   previous frame, normalised 0-1).
4. Writes a JSON event: `{timestamp, camera_id, features: {...}}`.

For ToF frames (`.depth.npy`), it computes `depth_mean`, `depth_std`,
and `change_score`.

`monitor_app.py` is an optional PyQt5 GUI that performs the same
processing in real time with live image previews and time-series charts.

### Stage 2 -- Dataset building (garden_audio/dataset_builder.py)

Called by `pipeline.py`. Walks `incoming_data/YYYY-MM-DD/` and for each
camera directory loads all JSON files, then computes:

- **cam01, cam02** (near cameras): per-feature summary (mean, std, min,
  max, linear trend over the day) for `brightness_mean`, `ndvi_mean`,
  `change_score`. Also splits brightness into morning/noon/evening thirds.
- **cam03** (ToF camera): same summary for `depth_mean`, `depth_std`,
  `change_score`.
- **Global**: brightness variance across all near-camera events,
  inter-camera brightness correlation, anomaly detection (events where
  change_score exceeds 2 sigma).

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

#### Parameter mappings

**Granular Sampling** (driven by cam01/cam02 motion and texture):
- Activity: `0.5 * change_score + 0.3 * brightness + 0.2 * ndvi`
- Presets: deep_haze -> warm_drift -> cloud -> shimmer
- Parameters: grain_density, grain_duration, pos_rate, pos_jitter, rate,
  rate_jitter, pan_width, lpf, delay_mix, delay_time, feedback,
  reverb_mix, dry_mix, amp_attack, amp_decay

**Spectral Resynthesis** (driven by vegetation and light):
- Activity: `0.4 * ndvi + 0.35 * brightness + 0.25 * change_score`
- Presets: spectral_drone -> warm_partials -> bright_swarm -> crystalline
- Parameters: voices_threshold, blur, warp_stretch, warp_shift, rate,
  feedback, tilt_db, glide_freq, reverb_mix, reverb_room, dry_mix,
  amp_attack, amp_decay

**Spectral Resonators** (driven by cam01/cam02 combined):
- Activity: `0.35 * change_score + 0.35 * brightness + 0.3 * ndvi`
- Presets: deep_gong -> warm_filter -> spectral_chord -> crystalline_ring
- Band frequencies: 6 bands from harmonic scale with root_freq, spread,
  and rotate parameters
- Parameters: root_freq, spread, rotate, rq, noise_mix, rate,
  excite_gain, morph_rate, reverb_mix, reverb_room, dry_mix,
  amp_attack, amp_decay

**Advanced Effects** (driven by cam01/cam02 combined):
- Activity: `0.35 * change_score + 0.35 * brightness + 0.3 * ndvi`
- Presets: ambient_wash -> dub_echo -> wide_chorus -> frozen_drive
- Parameters: algo (0-5 selects FX algorithm), mix, param1, param2,
  param3, stereo_width, level, rate, amp_attack, amp_decay

### Stage 4 -- SuperCollider rendering (supercollider/)

`pipeline.py` invokes `sclang run_daily.scd YYYY-MM-DD /path/to/project`.
The orchestrator iterates the 4 modules, sets `~configPath` in the
interpreter environment, and calls `executeFile` for each `.scd` module.

Each module:
1. Reads its JSON config via `File.readAllString(~configPath).parseJSON`.
2. Finds all WAV/AIF/AIFF files in `audio_batch_dir`.
3. Defines a SynthDef and builds an NRT Score.
4. Calls `Score.recordNRT` to render to `renders/YYYY-MM-DD/<module>.aiff`.

All renders are stereo, 48 kHz, 32-bit float AIFF. Duration is 120 seconds
by default (configurable via `RENDER_DURATION` in `config_generator.py`).

### Stage 5 -- Mastering (garden_audio/mastering.py)

After all four modules render, the pipeline applies LUFS-based mastering
(ITU-R BS.1770-4):

1. Measures combined integrated loudness across all four renders.
2. Computes a single gain to reach the target (-16 LUFS).
3. Applies the gain uniformly so relative volume between modules is preserved.
4. Caps at -1 dBTP true-peak ceiling to prevent clipping.
5. Writes the mastered audio back to the same AIFF files.

---

## How to Run

### Prerequisites

**Mac mini:**
- Python 3.9+ with venv
- SuperCollider installed (sclang accessible via PATH, Homebrew, or
  `/Applications/SuperCollider.app`)
- numpy, opencv-python, pyloudnorm, soundfile, python-osc, matplotlib
- PyQt5 (for monitor_app and explorer_gui)

**Raspberry Pi (each):**
- Raspberry Pi OS 64-bit Lite (Bookworm)
- Vimba X SDK for Linux ARM with GenTL installed
- `GENICAM_GENTL64_PATH=/opt/VimbaX/cti/arm64`
- Python venv with numpy, opencv-python-headless, vmbpy
- SSH key-based auth to the Mac (for passwordless SCP)

### 1. Set up the Mac

```bash
cd /Users/swc/Desktop/SWC/software
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up each Raspberry Pi

```bash
# On the Pi:
sudo apt update && sudo apt install -y python3-venv python3-pip
cd /home/pi/software
python3 -m venv venv
source venv/bin/activate
pip install numpy opencv-python-headless
pip install /path/to/VimbaX/api/python/vmbpy-*.whl

# Generate SSH key and copy to Mac for passwordless SCP:
ssh-keygen -t ed25519
ssh-copy-id swc@<mac-mini-ip>

# Edit the service file for your camera-id and Mac IP, then install:
sudo cp pi_capture/garden-capture.service /etc/systemd/system/
sudo cp pi_capture/garden-capture.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garden-capture.timer
```

Repeat for each Pi, changing `--camera-id` to `cam01`, `cam02`, or `cam03`.

### 3. Place field recordings

Before the first render, place audio files (WAV or AIFF) in each module's
permanent source folder. Each folder feeds one SuperCollider module and
is reused every day:

```bash
cp /path/to/granular_sources/*.wav    audio_batches/granular_sampling/
cp /path/to/spectral_sources/*.wav    audio_batches/spectral_resynthesis/
cp /path/to/resonator_sources/*.wav   audio_batches/spectral_resonators/
cp /path/to/fx_sources/*.wav          audio_batches/advanced_effects/
```

These source files are a combination of field recordings and synthetic
textures. They are curated once and do not change daily.

### 4. Run the pipeline manually

```bash
source venv/bin/activate
python -m garden_audio.pipeline --date 2026-03-24
```

This will:
- Read `incoming_data/2026-03-24/` and write `daily_datasets/2026-03-24/dataset.json`
- Write 4 config files to `module_configs/2026-03-24/`
- Validate that each `audio_batches/<module>/` folder contains source audio
- Invoke sclang to render 4 AIFF files to `renders/2026-03-24/`
- Master all renders to -16 LUFS
- Log everything to `daily_datasets/2026-03-24/pipeline.log`

### 5. Enable automatic daily runs on the Mac

```bash
cp launchd/com.swc.garden_audio.plist ~/Library/LaunchAgents/
cp launchd/com.swc.process_incoming.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.swc.garden_audio.plist
launchctl load ~/Library/LaunchAgents/com.swc.process_incoming.plist
```

The pipeline will run automatically at 07:00 every day, processing the
previous day's data (defaults to yesterday's date). The incoming image
processor runs every 15 minutes to keep JSON events up to date.

To disable:

```bash
launchctl unload ~/Library/LaunchAgents/com.swc.garden_audio.plist
launchctl unload ~/Library/LaunchAgents/com.swc.process_incoming.plist
```

---

## Testing Without Hardware

You can test the full pipeline without cameras by creating synthetic JSON
events manually:

```bash
mkdir -p incoming_data/2026-03-24/cam01
```

Create a few event files like `incoming_data/2026-03-24/cam01/cam01_081000.json`:

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

Repeat for cam02 and cam03 (cam03 events use `depth_mean`, `depth_std`,
`change_score` instead). Then place some WAV files in
the per-module `audio_batches/` subfolders and run the pipeline.

---

## Config JSON Format

Each module receives a JSON config with this structure:

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

The `params` object varies per module. All values are computed by
`config_generator.py` from the daily dataset via preset blending --
nothing is hardcoded. The `preset_blend` section records which two
presets were interpolated and at what position.

---

## SuperCollider Module Details

### 1. Granular Sampling (granular_sampling.scd)

SynthDef: `gardenMicrocosm`. Uses `TGrains` to slice field recordings
into grains. Parameters:
- Grain trigger rate (`Dust`) controlled by grain_density.
- `Phasor` scanning position with `TRand` jitter controlled by pos_rate
  and pos_jitter.
- Pitch varies per grain via `TExpRand` within the rate_jitter range.
- Random stereo panning per grain (pan_width).
- Post-grain chain: `RLPF` lowpass filter (lpf), `DelayC` delay line
  with feedback, `FreeVerb2` stereo reverb (reverb_mix).
- Dry/wet balance via dry_mix.
- Global ASR envelope over the full duration.

### 2. Spectral Resynthesis (spectral_resynthesis.scd)

SynthDef: `gardenPanharmonium`. Reads the source file via `PlayBuf` at
variable rate, then:
- FFT with `PV_MagAbove` (voice threshold gates low-energy bins).
- `PV_MagSmear` (spectral blur controlled by blur parameter).
- `PV_BinShift` (warp_stretch and warp_shift for spectral warping).
- IFFT back to time domain.
- `RLPF` glide filter (glide_freq).
- Broadband EQ tilt via `BLowShelf` / `BHiShelf` (tilt_db).
- Feedback loop from processed signal back into the FFT chain.
- `FreeVerb2` stereo reverb (reverb_mix, reverb_room).

### 3. Spectral Resonators (spectral_resonators.scd)

SynthDef: `gardenSMR`. Passes the source signal through a bank of 6
parallel `Resonz` filters:
- Frequencies at harmonically-related multiples of root_freq, selected
  by spread (harmonic interval) and rotate (scale offset).
- Each band has sinusoidal frequency drift via `SinOsc` at morph_rate.
- Reciprocal Q (rq) controls bandwidth.
- White noise excitation mixing (noise_mix) alongside the source.
- Alternating L/R band assignment for stereo spread.
- `FreeVerb2` reverb (reverb_mix, reverb_room).

### 4. Advanced Effects (advanced_effects.scd)

SynthDef: `gardenMultiFX`. Multi-algorithm FX module with 6 selectable
algorithms via `SelectX` (algo parameter):
- Algorithm 0: Hall Reverb (`FreeVerb2`, param1 = room, param2 = damp).
- Algorithm 1: Ping-Pong Delay (cross-fed `CombC`, param1 = time,
  param2 = feedback).
- Algorithm 2: Tape Echo (`CombC` with wow/flutter modulation,
  param1 = time, param2 = feedback, param3 = wow depth).
- Algorithm 3: Chorus (modulated `DelayC`, param1 = rate, param2 = depth).
- Algorithm 4: Overdrive (tanh distortion, param1 = gain, param2 = tone).
- Algorithm 5: FFT Freezer (`PV_MagFreeze` + `PV_MagSmear`).
- Three macro knobs (param1-3) control each algorithm differently.
- Mid-side stereo width processing (stereo_width).
- Final `Limiter` at 0.95.

### Explorer GUI (exploration/)

A real-time tuning interface for auditioning each SC module with live
parameter control:

```bash
source venv/bin/activate
python -m exploration.explorer_gui
```

Features:
- Boots SuperCollider via `explorer_server.scd` (defines all 4 SynthDefs,
  listens for OSC on port 57120).
- Load a daily dataset to see data features and computed activity score.
- Per-module parameter sliders with live OSC feedback to SC.
- Waveform display with playhead tracking.
- Preset library: save/load parameter sets paired with data conditions.
- k-NN proposal engine: given loaded data features, suggests the closest
  saved presets by weighted Euclidean distance.
- Saved presets in `user_presets/` are also used by `config_generator.py`
  during automated pipeline runs.

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
  specific data conditions and save them; the pipeline reuses those
  presets when similar conditions recur.
- Modular processing -- each SC module is independent and can be
  developed, tested, or replaced separately.
- Network independence -- Pis buffer locally if the Mac is unreachable.
- Robust logging -- every pipeline run logs to a dated log file.

---

## Folder Naming Conventions

Runtime folders use ISO date format `YYYY-MM-DD`:
- `incoming_data/2026-03-24/cam01/cam01_081000.json`
- `daily_datasets/2026-03-24/dataset.json`
- `module_configs/2026-03-24/granular_sampling.json`
- `renders/2026-03-24/granular_sampling.aiff`

Source audio folders are permanent and named by SC module:
- `audio_batches/granular_sampling/field_recording_01.wav`
- `audio_batches/spectral_resynthesis/texture_01.aiff`
- `audio_batches/spectral_resonators/excitation_01.wav`
- `audio_batches/advanced_effects/dry_source_01.wav`

---

## What Is Not Yet Implemented

- **Server upload** (pipeline step 8): rendered files are stored locally
  but not uploaded anywhere. Add an SCP/rsync/S3 step to `pipeline.py`.
- **Local buffer retry on Pi**: `capture_and_send.py` buffers events
  locally when the Mac is unreachable, but does not yet retry sending
  them when the connection recovers.
- **ToF camera deployment**: `capture_and_send_tof.py` exists and handles
  Arducam ToF capture, but has not been field-tested. The ToF features
  in `dataset_builder.py` (`edge_density`, `geometry_score`) are not
  produced by the current Mac-side processing, which outputs `depth_mean`,
  `depth_std`, `change_score` instead.
- **Seasonal baseline**: a seasonal rolling average for NDVI / brightness
  would give more meaningful relative activity scores.
