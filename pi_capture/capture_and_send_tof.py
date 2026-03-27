"""Continuous headless ToF depth capture and transfer to Mac mini.

Runs as a long-lived process, capturing one depth frame every 10 minutes
(configurable), saving depth/confidence/amplitude as .npy + RGB as JPEG
+ metadata sidecar, and SCPing all files to the Mac.

Camera ID is auto-detected from hostname: node3 -> cam03, etc.

Usage:
    python capture_and_send_tof.py --mac-host 192.168.178.25
    python capture_and_send_tof.py --mac-host 192.168.178.25 --interval 300

Dependencies:
    pip install ArducamDepthCamera numpy opencv-python-headless
"""

import argparse
import json
import logging
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("pi_capture_tof")

REMOTE_BASE = "/Users/swc/Desktop/SWC/software/incoming_data"
SSH_USER = "swc"
JPEG_QUALITY = 90

_shutdown = False


def _signal_handler(sig, frame):
    global _shutdown
    _shutdown = True
    log.info("Shutdown requested, finishing current cycle...")


def _camera_id_from_hostname():
    """Derive camera ID from hostname: node3 -> cam03, etc."""
    hostname = socket.gethostname().lower()
    for i in range(1, 100):
        if f"node{i}" in hostname:
            return f"cam{i:02d}"
    return "cam03"


def _capture_tof_frame():
    """Capture one ToF frame. Returns (depth, confidence, amplitude, meta) or raises."""
    import ArducamDepthCamera as ac

    cam = ac.ArducamCamera()
    ret = cam.open(ac.TOFConnect.CSI, 0)
    if ret != 0:
        raise RuntimeError(f"Failed to open ToF camera (error {ret})")

    ret = cam.start(ac.TOFOutput.DEPTH)
    if ret != 0:
        cam.close()
        raise RuntimeError(f"Failed to start ToF camera (error {ret})")

    depth_range = cam.getControl(ac.TOFControl.RANGE)
    info = cam.getCameraInfo()

    frame = cam.requestFrame(5000)
    if frame is None:
        cam.stop()
        cam.close()
        raise RuntimeError("ToF frame request timed out")

    try:
        depth = frame.getDepthData().copy()
        confidence = frame.getConfidenceData().copy()
        amplitude = frame.getAmplitudeData().copy()
    except Exception as exc:
        cam.releaseFrame(frame)
        cam.stop()
        cam.close()
        raise RuntimeError(f"Failed to read ToF frame data: {exc}")

    cam.releaseFrame(frame)
    cam.stop()
    cam.close()

    meta = {
        "camera_type": "tof",
        "width": int(info.width),
        "height": int(info.height),
        "depth_range": float(depth_range),
        "depth_mean": float(np.nanmean(depth)),
        "depth_std": float(np.nanstd(depth)),
    }

    return depth, confidence, amplitude, meta


def _capture_rgb():
    """Attempt to capture an RGB frame via picamera2. Returns BGR numpy or None."""
    try:
        from picamera2 import Picamera2
        picam = Picamera2()
        config = picam.create_still_configuration(main={"size": (640, 480)})
        picam.configure(config)
        picam.start()
        time.sleep(0.5)
        arr = picam.capture_array()
        picam.stop()
        picam.close()
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        elif arr.ndim == 3 and arr.shape[2] == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        return arr
    except Exception as exc:
        log.warning("RGB capture unavailable: %s", exc)
        return None


def _send_to_mac(file_paths, camera_id, mac_host):
    """SCP a list of files to the Mac."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    remote_dir = f"{REMOTE_BASE}/{date_str}/{camera_id}"

    subprocess.run(
        ["ssh", f"{SSH_USER}@{mac_host}", "mkdir", "-p", remote_dir],
        check=False,
        timeout=15,
    )
    cmd = ["scp"] + [str(p) for p in file_paths] + [
        f"{SSH_USER}@{mac_host}:{remote_dir}/"
    ]
    result = subprocess.run(cmd, check=False, timeout=120)
    return result.returncode == 0


def _buffer_locally(file_paths, camera_id):
    """Copy files to a local buffer when the Mac is unreachable."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    buf_dir = Path.home() / "capture_buffer" / date_str / camera_id
    buf_dir.mkdir(parents=True, exist_ok=True)
    for p in file_paths:
        shutil.copy2(str(p), str(buf_dir / p.name))
    log.info("Buffered locally: %s (%d files)", buf_dir, len(file_paths))


def _capture_cycle(camera_id, mac_host):
    """Run one capture-and-send cycle. Returns status dict."""
    now = datetime.now(timezone.utc)
    ts_slug = now.strftime("%H%M%S")

    log.info("Capturing ToF frame for %s...", camera_id)
    depth, confidence, amplitude, meta = _capture_tof_frame()
    meta["timestamp"] = now.isoformat()

    tmp_dir = Path(tempfile.mkdtemp(prefix="pi_tof_"))
    prefix = f"{camera_id}_{ts_slug}"

    depth_path = tmp_dir / f"{prefix}.depth.npy"
    conf_path = tmp_dir / f"{prefix}.confidence.npy"
    amp_path = tmp_dir / f"{prefix}.amplitude.npy"
    meta_path = tmp_dir / f"{prefix}.meta.json"

    np.save(str(depth_path), depth)
    np.save(str(conf_path), confidence)
    np.save(str(amp_path), amplitude)
    meta_path.write_text(json.dumps(meta, indent=2))

    files_to_send = [depth_path, conf_path, amp_path, meta_path]

    rgb = _capture_rgb()
    if rgb is not None:
        jpg_path = tmp_dir / f"{prefix}.jpg"
        cv2.imwrite(str(jpg_path), rgb, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        files_to_send.append(jpg_path)
        log.info("RGB image captured (%dx%d)", rgb.shape[1], rgb.shape[0])

    total_kb = sum(p.stat().st_size for p in files_to_send) / 1024
    log.info(
        "Saved %s: depth mean=%.3fm, std=%.3fm, %d files (%.0f KB)",
        prefix, meta["depth_mean"], meta["depth_std"],
        len(files_to_send), total_kb,
    )

    sent = _send_to_mac(files_to_send, camera_id, mac_host)
    if not sent:
        _buffer_locally(files_to_send, camera_id)

    shutil.rmtree(str(tmp_dir), ignore_errors=True)

    return {
        "time": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "prefix": prefix,
        "depth_mean": meta["depth_mean"],
        "depth_std": meta["depth_std"],
        "total_kb": total_kb,
        "has_rgb": rgb is not None,
        "sent": sent,
    }


def _print_status(camera_id, history, total_sent, total_failed):
    """Print a status summary to the console."""
    print()
    print("=" * 72)
    print(f"  Camera: {camera_id} (ToF)   |   Total: {len(history)} frames"
          f"   |   Sent: {total_sent}   |   Failed: {total_failed}")
    print("-" * 72)
    print(f"  {'#':>3}  {'Time':>22}  {'Depth Mean':>10}  "
          f"{'Depth Std':>9}  {'KB':>6}  {'RGB':>3}  {'Status':<8}")
    print("-" * 72)
    for i, h in enumerate(history, 1):
        status = "OK" if h["sent"] else "BUFFERED"
        rgb_flag = "Y" if h["has_rgb"] else "N"
        print(
            f"  {i:>3}  {h['time']:>22}  {h['depth_mean']:>10.3f}  "
            f"{h['depth_std']:>9.3f}  {h['total_kb']:>5.0f}  "
            f"{rgb_flag:>3}  {status:<8}"
        )
    print("=" * 72)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Continuous ToF depth capture + send to Mac"
    )
    parser.add_argument(
        "--camera-id",
        default=None,
        help="Camera identifier. Default: auto from hostname "
             "(node3 -> cam03, ...)",
    )
    parser.add_argument(
        "--mac-host",
        required=True,
        help="IP or hostname of the Mac mini",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=600,
        help="Seconds between captures (default: 600 = 10 minutes)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    camera_id = args.camera_id or _camera_id_from_hostname()
    log.info("Camera ID: %s (hostname: %s)", camera_id, socket.gethostname())

    history = []
    total_sent = 0
    total_failed = 0

    log.info(
        "Starting continuous ToF capture: camera=%s, interval=%ds, mac=%s",
        camera_id, args.interval, args.mac_host,
    )

    while not _shutdown:
        try:
            result = _capture_cycle(camera_id, args.mac_host)
            history.append(result)
            if result["sent"]:
                total_sent += 1
            else:
                total_failed += 1
            _print_status(camera_id, history, total_sent, total_failed)
        except Exception as exc:
            log.error("Capture cycle failed: %s", exc)
            total_failed += 1

        if _shutdown:
            break

        log.info(
            "Next capture in %d seconds (Ctrl+C to stop)...", args.interval
        )
        for _ in range(args.interval):
            if _shutdown:
                break
            time.sleep(1)

    log.info(
        "Stopped. Total frames: %d (sent: %d, buffered: %d)",
        len(history), total_sent, total_failed,
    )


if __name__ == "__main__":
    main()
