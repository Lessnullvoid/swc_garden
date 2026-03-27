# Raspberry Pi Setup and Deployment

This guide covers deploying the headless camera capture system on
Raspberry Pi 4 (2 GB). Each Pi captures one frame every 10 minutes
and sends it to the Mac mini for calibration and feature extraction.

## Requirements

- **Raspberry Pi 4** (2 GB or more)
- **Raspberry Pi OS 64-bit** (Bookworm or Bullseye). The 64-bit image is
  required because Vimba X ARM GenTL libraries and VmbPy need aarch64.
- **Alvium 1800 U-501m NIR** connected via USB 3
- **WiFi network** with access to the Mac mini
- **SSH key-based auth** to the Mac for passwordless SCP

---

## 1. Install Vimba X SDK for Linux ARM

Download **Vimba X for Linux ARM** from
[Allied Vision Software Downloads](https://www.alliedvision.com/en/products/vimba-sdk/)
and copy the tar archive to the Pi (e.g. via SCP or USB stick).

Extract and move to `/opt`:

```bash
cd ~
tar -xf VimbaX_2026-1.tar
sudo mv VimbaX_2026-1 /opt/VimbaX
```

Run the GenTL install script. This registers the transport layer paths
system-wide via `/etc/profile.d/` and installs udev rules for Allied
Vision USB cameras:

```bash
cd /opt/VimbaX/cti
sudo ./Install_GenTL_Path.sh
```

Add the GenTL path to `~/.bashrc` so it is available in all shells
(including inside Python venvs):

```bash
echo 'export GENICAM_GENTL64_PATH="/opt/VimbaX/cti"' >> ~/.bashrc
source ~/.bashrc
```

Reboot for the udev rules to take effect:

```bash
sudo reboot
```

After reboot, verify the SDK is installed correctly:

```bash
echo $GENICAM_GENTL64_PATH
# /opt/VimbaX/cti

ls /opt/VimbaX/cti/*.cti
# VimbaCSITL.cti  VimbaCameraSimulatorTL.cti  VimbaGigETL.cti  VimbaUSBTL.cti

/opt/VimbaX/bin/ListCameras_VmbC
```

The `.cti` files live directly in `/opt/VimbaX/cti/` (not in an `arm64`
subdirectory). `ListCameras_VmbC` should show the Alvium camera plus
three bundled camera simulators.

## 2. Python and dependencies

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
```

Create a venv and install packages (no PyQt5 needed for headless capture):

```bash
mkdir -p /home/pi/software
cd /home/pi/software
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install numpy opencv-python-headless
pip install /opt/VimbaX/api/python/vmbpy-1.2.1-py3-none-manylinux_2_27_aarch64.whl
```

Verify VmbPy can see the camera (must be plugged into a USB 3 port):

```bash
python3 -c "
from vmbpy import VmbSystem
with VmbSystem.get_instance() as vmb:
    cams = vmb.get_all_cameras()
    print(f'Found {len(cams)} camera(s)')
    for c in cams:
        print(f'  {c.get_id()} - {c.get_name()}')
"
```

Expected output includes `DEV_1AB22C0B039D - Allied Vision 1800 U-501m NIR`
(the real camera) plus three bundled camera simulators.

## 3. Enable Remote Login on the Mac

The Pi needs SSH access to the Mac for SCP file transfers. On the Mac:

1. Open **System Settings > General > Sharing**.
2. Toggle **Remote Login** to ON.
3. Ensure your user (`swc`) is in the allowed users list (or set
   "Allow access for: All users").

Verify from the Pi: `ssh swc@<mac-ip> echo ok` (will ask for password
the first time -- this is expected before key setup below).

## 4. SSH key setup (passwordless SCP to Mac)

On the Pi, generate a key and copy it to the Mac:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
ssh-copy-id swc@<mac-ip>
```

Enter the Mac password when prompted (last time you will need it).

Test: `ssh swc@<mac-ip> echo ok` should print `ok` without asking
for a password. If it does, passwordless SCP is working.

## 5. Initial calibration (one-time, done by you)

Calibration is performed once per camera at deployment. The camera ID is
auto-detected from the hostname (`node1` -> `cam01`, `node2` -> `cam02`, etc.),
so you do not need to pass `--camera-id` manually.

### Live view (position the camera)

Start the MJPEG stream to check framing in a browser:

```bash
cd ~/software
source venv/bin/activate
python pi_capture/live_view.py
```

Open `http://<pi-ip>:8080` on the Mac to see the live feed.
Adjust the camera angle and focus, then `Ctrl+C` to stop.

If multiple cameras are connected, specify which one:

```bash
python pi_capture/live_view.py --camera-id DEV_1AB22C000B81
```

### Dark frame

Cap the lens (or point at a completely dark surface), then:

```bash
python pi_capture/capture_calibration.py --mode dark --mac-host 192.168.178.25
```

This captures 16 frames at minimum exposure/gain, averages them,
saves `calibration/<camXX>_dark.npy` locally, and SCPs it to the Mac.

### Flat field

Point the camera at a uniformly illuminated white surface (overcast sky
or a white card with even lighting), then:

```bash
python pi_capture/capture_calibration.py --mode flat --mac-host 192.168.178.25
```

This captures 16 frames, subtracts the dark frame (if available),
normalizes so mean = 1.0, saves `calibration/<camXX>_flat.npy`,
and SCPs it to the Mac.

### Verify locally and on the Mac

```bash
# On the Pi:
ls -la ~/software/calibration/

# On the Mac:
ls -la /Users/swc/Desktop/SWC/software/calibration/
# cam01_dark.npy  cam01_flat.npy
# cam02_dark.npy  cam02_flat.npy
```

If you need to redo calibration later, just run the same commands again.
The `.npy` files are overwritten in place.

## 6. Test capture manually

Before enabling the systemd timer, test a single capture cycle:

```bash
cd ~/software
source venv/bin/activate
python pi_capture/capture_and_send.py --mac-host 192.168.178.25
```

This auto-detects the camera ID from the hostname, captures one frame,
and sends it to the Mac. Check the Mac's `incoming_data/` for the file.
`Ctrl+C` to stop after the first frame.

## 7. Install systemd services

Copy the service and timer files:

```bash
sudo cp pi_capture/garden-capture.service /etc/systemd/system/
sudo cp pi_capture/garden-capture.timer   /etc/systemd/system/
```

Edit the service to set your Mac IP (camera ID is auto-detected):

```bash
sudo systemctl edit garden-capture.service
```

Or edit `/etc/systemd/system/garden-capture.service` directly -- change
`--mac-host 192.168.178.25` to your Mac's IP address.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now garden-capture.timer
```

Verify it is scheduled:

```bash
systemctl list-timers garden-capture.timer
```

## 8. What the Pi does (automatic after setup)

Every 10 minutes the systemd timer fires `capture_and_send.py`, which:

1. Opens the camera, sets `ExposureAuto = Continuous`, warms up
   15 frames for auto-exposure to converge, and grabs the final frame.
2. **Checks ambient light** using the camera as its own sensor (see
   section 8a below). If too dark, the frame is discarded and the
   process enters dark mode.
3. Saves the frame as JPEG (quality 90) + a `.meta.json` sidecar
   containing `exposure_us`, `gain_db`, and `pixel_format`.
4. SCPs both files to the Mac at
   `incoming_data/YYYY-MM-DD/<camera_id>/`.
5. If SCP fails (Mac offline, WiFi down), buffers both files locally
   in `~/capture_buffer/` for manual recovery later.

No feature extraction or calibration happens on the Pi.

### 8a. Daylight-gated capture

The capture script uses the camera's own auto-exposure convergence
value as an ambient light sensor. In daylight, auto-exposure settles
at 1,000--10,000 us (1--10 ms). At night it climbs to 760,000+ us
(760 ms+), a 100--600x increase that is trivial to threshold.

**Two modes:**

- **DAYLIGHT** -- normal capture every `--interval` seconds (default
  600s = 10 min). Frames are encoded and sent to the Mac.
- **DARK** -- the frame is discarded (not encoded, not sent). The
  process probes every `--dark-interval` seconds (default 1800s =
  30 min) by capturing a throwaway frame and reading the exposure.
  When exposure drops below `--light-threshold`, daylight mode
  resumes automatically.

**Hysteresis thresholds** prevent rapid toggling at dawn/dusk:

| Flag | Default | Meaning |
|------|---------|---------|
| `--dark-threshold` | 100000 us (100 ms) | Exposure above this enters dark mode |
| `--light-threshold` | 50000 us (50 ms) | Exposure below this resumes daylight mode |
| `--dark-interval` | 1800 s (30 min) | Probe frequency while in dark mode |

An additional safety check flags the scene as dark if the raw mean
pixel value drops below 15 (out of 255), regardless of exposure time.

**State transitions are logged** to journald for monitoring:

```
[INFO] Dark mode entered: exposure=764150us > threshold=100000us, mean_px=97.3
[INFO] Dark probe 1: exposure=766083us, mean_px=96.8 (need exposure < 50000us to resume)
[INFO] Daylight resumed: exposure=6647us < threshold=50000us (after 4 dark probes)
```

**Folder persistence:** the existing `incoming_data/YYYY-MM-DD/camXX/`
structure already creates a new date-folder each day. When the process
wakes from dark mode the next morning, the new date directory is
created automatically via `mkdir -p`.

**Custom thresholds:** to override defaults, edit the `ExecStart` line
in the systemd service:

```bash
sudo systemctl edit garden-capture.service
```

Add flags as needed:

```ini
[Service]
ExecStart=
ExecStart=/home/pi/software/venv/bin/python pi_capture/capture_and_send.py \
    --mac-host 192.168.178.25 \
    --dark-threshold 80000 \
    --light-threshold 40000 \
    --dark-interval 900
```

## 9. What the Mac does (automatic)

The Mac runs two launchd agents (installed once, start on boot):

- **process_incoming** (every 15 min): scans `incoming_data/` for new
  images, applies dark/flat calibration, extracts features
  (brightness, NDVI proxy, change score), and writes JSON events.

- **garden_audio** (22:00 daily): aggregates the day's events,
  generates SuperCollider configs, and renders audio files.

Install the processing agent on the Mac:

```bash
cp launchd/com.swc.process_incoming.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.swc.process_incoming.plist
```

---

## Node 3: Arducam ToF Depth Camera

Node 3 uses an **Arducam PiVistation RGBD ToF** depth camera instead of an
Alvium. It connects via MIPI CSI-2 and uses a completely different SDK.
The steps below replace sections 1-2 and 5-7 for node3 only. Sections 3-4
(Mac Remote Login + SSH keys) still apply.

### N3.1 Install Arducam ToF SDK

The PiVistation kit comes pre-configured with the SDK. If you need to
install it manually:

```bash
sudo pip3 install ArducamDepthCamera
```

Verify:

```bash
python3 -c "import ArducamDepthCamera as ac; print('SDK version:', ac.__version__)"
```

### N3.2 Python environment

```bash
mkdir -p /home/pi/software
cd /home/pi/software
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install numpy opencv-python-headless
pip install ArducamDepthCamera
```

### N3.3 Copy scripts from the Mac

From the Mac:

```bash
scp -r pi_capture/ pi@node3.local:~/software/pi_capture/
```

### N3.4 Live view (position the camera)

```bash
cd ~/software
source venv/bin/activate
python pi_capture/live_view_tof.py
```

Open `http://node3.local:8080` in a browser on the Mac. The stream shows
depth colormap (left) and amplitude (right) side by side.
Adjust camera angle, then `Ctrl+C` to stop.

### N3.5 Test capture

```bash
python pi_capture/capture_and_send_tof.py --mac-host 192.168.178.25
```

This auto-detects `cam03` from hostname `node3`, captures one ToF frame
(depth + confidence + amplitude .npy files + RGB .jpg), and SCPs
everything to the Mac. `Ctrl+C` after the first frame to verify.

### N3.6 Continuous operation

```bash
python pi_capture/capture_and_send_tof.py --mac-host 192.168.178.25 --interval 600
```

Or install as a systemd service (create `garden-capture-tof.service`
similar to `garden-capture.service` but with the ToF capture command).

### N3.7 What node3 sends to the Mac

Each capture cycle produces (all in `incoming_data/YYYY-MM-DD/cam03/`):

- `cam03_HHMMSS.depth.npy` -- depth map (float32, 640x480)
- `cam03_HHMMSS.confidence.npy` -- confidence map (float32)
- `cam03_HHMMSS.amplitude.npy` -- amplitude map (float32)
- `cam03_HHMMSS.jpg` -- RGB image (if picamera2 is available)
- `cam03_HHMMSS.meta.json` -- metadata (timestamp, camera_type: "tof", depth stats)

No calibration files needed -- the ToF sensor is factory-calibrated.

---

## 10. Troubleshooting

- **"No TL detected" / VmbSystemError / VmbError.NoTL:**
  Ensure `GENICAM_GENTL64_PATH` is set to `/opt/VimbaX/cti` (the directory
  containing the `.cti` files directly -- there is no `arm64` subdirectory).
  Run `echo $GENICAM_GENTL64_PATH` and `ls $GENICAM_GENTL64_PATH/*.cti` to
  confirm. If the path is wrong, fix it in `~/.bashrc` and `source ~/.bashrc`.
  Also check that `/etc/profile.d/VimbaX_GenTL_Path_64bit.sh` exists (created
  by `Install_GenTL_Path.sh`).

- **Camera not found:**
  Check USB 3 connection. Ensure no other process is using the camera.

- **SCP failures:**
  Check `journalctl -u garden-capture.service` for errors. Verify
  SSH key auth works: `ssh swc@<mac-ip> echo ok`. Buffered images
  are in `~/capture_buffer/` and can be manually copied to the Mac.

- **No events appearing on Mac:**
  Check that `incoming_data/` contains `.jpg` + `.meta.json` pairs.
  Run `python process_incoming.py` manually to see logs.

- **Pi appears to stop capturing at night:**
  This is expected. The daylight gate detects darkness via auto-exposure
  and pauses image broadcast until morning. Check journald to confirm:
  `journalctl -u garden-capture.service | grep "Dark mode"`.
  To verify it is still alive and probing:
  `journalctl -u garden-capture.service | grep "Dark probe"`.

- **Captures stop too early or resume too late:**
  Adjust the thresholds. Lower `--dark-threshold` to enter dark mode
  sooner (less tolerant of dim light). Raise `--light-threshold` to
  require brighter conditions before resuming. Check current exposure
  values in the `.meta.json` files or journal logs to calibrate.

## Summary

| Step | What | Who |
|------|------|-----|
| 1 | Install Vimba X SDK + GenTL on Pi | You (once) |
| 2 | Create venv, install numpy, opencv, vmbpy | You (once) |
| 3 | Enable Remote Login on the Mac | You (once) |
| 4 | Set up SSH key auth (Pi to Mac) | You (once) |
| 5 | Live view + calibration (dark + flat, auto-sent to Mac) | You (once per camera) |
| 6 | Test manual capture | You (once) |
| 7 | Install systemd service + timer | You (once) |
| 8 | Pi captures + sends images (daylight only, auto-pauses at night) | Automatic |
| 9 | Mac processes images + renders audio | Automatic |
