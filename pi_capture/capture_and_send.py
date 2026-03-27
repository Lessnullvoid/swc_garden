"""Continuous headless frame capture and transfer to Mac mini.

Runs as a long-lived process, capturing one frame every 10 minutes
(configurable), saving as JPEG + metadata sidecar, and SCPing both
files to the Mac. Prints a running status summary after each capture.

Uses the camera as its own ambient-light sensor: when auto-exposure
climbs above --dark-threshold the scene is too dark to produce useful
images, so broadcast stops and the process enters a low-frequency
probing mode. When exposure drops below --light-threshold, normal
capture resumes. Hysteresis between the two thresholds prevents
rapid toggling at dawn/dusk.

Camera ID is auto-detected from hostname: node1 -> cam01, node2 -> cam02, etc.
Can be overridden with --camera-id.

Usage:
    python capture_and_send.py --mac-host 192.168.178.25
    python capture_and_send.py --mac-host 192.168.178.25 --device-id DEV_1AB22C0B039D
    python capture_and_send.py --camera-id cam01 --mac-host 192.168.178.25 --interval 300
    python capture_and_send.py --mac-host 192.168.178.25 --dark-threshold 80000 --light-threshold 40000

Environment:
    GENICAM_GENTL64_PATH must point to the Vimba X ARM GenTL directory.
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

log = logging.getLogger("pi_capture")

REMOTE_BASE = "/Users/swc/Desktop/SWC/software/incoming_data"
SSH_USER = "swc"
JPEG_QUALITY = 90

_shutdown = False
BRIGHTNESS_FLOOR = 15


def _camera_id_from_hostname() -> str:
    """Derive camera ID from hostname: node1 -> cam01, node2 -> cam02, etc."""
    hostname = socket.gethostname().lower()
    for i in range(1, 100):
        if f"node{i}" in hostname:
            return f"cam{i:02d}"
    return "cam01"


def _signal_handler(sig, frame):
    global _shutdown
    _shutdown = True
    log.info("Shutdown requested, finishing current cycle...")


def _frame_to_numpy(frame, cam_width: int, cam_height: int) -> np.ndarray:
    """Extract a 2-D uint8 array from a VmbPy Frame, with multiple fallbacks.

    cam_width / cam_height come from the camera's Width/Height features
    and are used when the frame object lacks its own dimension metadata.
    """
    import vmbpy

    try:
        return frame.as_numpy_ndarray().squeeze().copy()
    except Exception:
        pass

    try:
        frame.convert_pixel_format(vmbpy.PixelFormat.Mono8)
        return frame.as_numpy_ndarray().squeeze().copy()
    except Exception:
        pass

    buf = frame.get_buffer()
    expected = cam_width * cam_height
    buf_len = len(buf)
    if buf_len >= expected * 2:
        arr = np.array(buf[:expected * 2], dtype=np.uint8).view(np.uint16)
        arr = (arr >> 2).astype(np.uint8)
    else:
        arr = np.array(buf[:expected], dtype=np.uint8)
    return arr.reshape(cam_height, cam_width).copy()


def _is_too_dark(frame: np.ndarray, meta: dict, dark_threshold: float) -> bool:
    """Return True if the scene is too dark to produce a useful capture.

    Uses two signals:
    1. Auto-exposure time exceeding dark_threshold (primary).
    2. Mean pixel value below BRIGHTNESS_FLOOR -- catches the case where
       even maximum auto-exposure cannot adequately illuminate the scene.
    """
    if meta.get("exposure_us", 0) > dark_threshold:
        return True
    if np.mean(frame) < BRIGHTNESS_FLOOR:
        return True
    return False


def _capture_frame(device_id: str | None = None) -> tuple:
    """Capture a single frame with auto-exposure; return (frame, metadata).

    Uses a short burst of frames so the camera's auto-exposure algorithm
    has time to converge before we grab the final image.
    """
    import vmbpy

    WARMUP_FRAMES = 15

    with vmbpy.VmbSystem.get_instance() as vmb:
        cameras = vmb.get_all_cameras()
        if not cameras:
            raise RuntimeError("No cameras detected")

        cam_obj = None
        if device_id:
            for c in cameras:
                if c.get_id() == device_id:
                    cam_obj = c
                    break
            if cam_obj is None:
                available = [c.get_id() for c in cameras]
                raise RuntimeError(
                    f"Device {device_id} not found. Available: {available}"
                )
        else:
            cam_obj = cameras[0]

        with cam_obj as cam:
            try:
                pf_feat = cam.get_feature_by_name("PixelFormat")
                log.info("Current PixelFormat: %s", pf_feat.get())
                pf_feat.set("Mono8")
                log.info("Set PixelFormat to Mono8")
            except Exception as exc:
                log.warning("PixelFormat handling: %s", exc)

            cam_w = int(cam.get_feature_by_name("Width").get())
            cam_h = int(cam.get_feature_by_name("Height").get())
            log.info("Sensor dimensions: %dx%d", cam_w, cam_h)

            try:
                cam.get_feature_by_name("ExposureAuto").set("Off")
                cam.get_feature_by_name("ExposureTime").set(10000.0)
                log.info("Seeded ExposureTime to 10000us before auto-exposure")
            except Exception as exc:
                log.warning("Could not seed ExposureTime: %s", exc)

            try:
                cam.get_feature_by_name("ExposureAuto").set("Continuous")
            except Exception:
                log.warning("Could not set ExposureAuto to Continuous")

            arr = None
            for i in range(WARMUP_FRAMES):
                try:
                    frame = cam.get_frame(timeout_ms=10000)
                except Exception as exc:
                    log.warning("Frame grab %d failed: %s", i + 1, exc)
                    continue

                try:
                    if frame.get_status() != vmbpy.FrameStatus.Complete:
                        continue
                except Exception:
                    pass

                candidate = _frame_to_numpy(frame, cam_w, cam_h)
                if candidate.size > 0:
                    arr = candidate

            if arr is None:
                raise RuntimeError("No valid frame after %d attempts" % WARMUP_FRAMES)

            meta = {}
            try:
                meta["exposure_us"] = cam.get_feature_by_name(
                    "ExposureTime"
                ).get()
            except Exception:
                meta["exposure_us"] = 0.0

            try:
                meta["gain_db"] = cam.get_feature_by_name("Gain").get()
            except Exception:
                meta["gain_db"] = 0.0

            try:
                pf = cam.get_feature_by_name("PixelFormat").get()
                meta["pixel_format"] = str(pf)
            except Exception:
                meta["pixel_format"] = "Mono8"

            log.info(
                "Auto-exposure settled: %.0f us (after %d warmup frames)",
                meta["exposure_us"], WARMUP_FRAMES,
            )
            return arr, meta


def _send_to_mac(
    jpg_path: Path,
    meta_path: Path,
    camera_id: str,
    mac_host: str,
) -> bool:
    """SCP the JPEG and metadata sidecar to the Mac."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    remote_dir = f"{REMOTE_BASE}/{date_str}/{camera_id}"

    subprocess.run(
        ["ssh", f"{SSH_USER}@{mac_host}", "mkdir", "-p", remote_dir],
        check=False,
        timeout=15,
    )
    result = subprocess.run(
        [
            "scp",
            str(jpg_path),
            str(meta_path),
            f"{SSH_USER}@{mac_host}:{remote_dir}/",
        ],
        check=False,
        timeout=60,
    )
    return result.returncode == 0


def _buffer_locally(jpg_path: Path, meta_path: Path, camera_id: str) -> None:
    """Copy image + metadata to a local buffer when the Mac is unreachable."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    buf_dir = Path.home() / "capture_buffer" / date_str / camera_id
    buf_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(jpg_path), str(buf_dir / jpg_path.name))
    shutil.copy2(str(meta_path), str(buf_dir / meta_path.name))
    log.info("Buffered locally: %s", buf_dir)


def _encode_and_send(
    frame: np.ndarray,
    meta: dict,
    camera_id: str,
    mac_host: str,
) -> dict:
    """Encode frame as JPEG, write metadata sidecar, and SCP to the Mac.

    The caller has already captured the frame and checked the light level.
    Returns a status dict for the history log.
    """
    now = datetime.now(timezone.utc)
    ts_slug = now.strftime("%H%M%S")
    meta["timestamp"] = now.isoformat()

    tmp_dir = Path(tempfile.mkdtemp(prefix="pi_cap_"))
    jpg_name = f"{camera_id}_{ts_slug}.jpg"
    meta_name = f"{camera_id}_{ts_slug}.meta.json"
    jpg_path = tmp_dir / jpg_name
    meta_path = tmp_dir / meta_name

    cv2.imwrite(
        str(jpg_path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    )
    meta_path.write_text(json.dumps(meta, indent=2))

    size_kb = jpg_path.stat().st_size / 1024
    sent = _send_to_mac(jpg_path, meta_path, camera_id, mac_host)

    if not sent:
        _buffer_locally(jpg_path, meta_path, camera_id)

    shutil.rmtree(str(tmp_dir), ignore_errors=True)

    return {
        "time": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "file": jpg_name,
        "size_kb": size_kb,
        "exposure_us": meta.get("exposure_us", 0),
        "gain_db": meta.get("gain_db", 0),
        "sent": sent,
    }


def _print_status(
    camera_id: str,
    history: list,
    total_sent: int,
    total_failed: int,
    is_dark: bool,
    dark_interval: int,
):
    """Print a status summary to the console."""
    mode = "DARK (probing every %dm)" % (dark_interval // 60) if is_dark else "DAYLIGHT"
    print()
    print("=" * 65)
    print(f"  Camera: {camera_id}   |   Mode: {mode}")
    print(f"  Total: {len(history)} frames"
          f"   |   Sent: {total_sent}   |   Failed: {total_failed}")
    print("-" * 65)
    print(f"  {'#':>3}  {'Time':>22}  {'File':<26}  {'KB':>6}  {'Status':<8}")
    print("-" * 65)
    for i, h in enumerate(history, 1):
        status = "OK" if h["sent"] else "BUFFERED"
        print(
            f"  {i:>3}  {h['time']:>22}  {h['file']:<26}  "
            f"{h['size_kb']:>5.0f}  {status:<8}"
        )
    print("=" * 65)
    if history:
        last = history[-1]
        print(f"  Last: exposure={last['exposure_us']:.0f}us  gain={last['gain_db']:.1f}dB")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Continuous Pi capture + send to Mac"
    )
    parser.add_argument(
        "--camera-id",
        default=None,
        help="Camera identifier (cam01, cam02). Default: auto from hostname "
             "(node1 -> cam01, node2 -> cam02, ...)",
    )
    parser.add_argument(
        "--mac-host",
        required=True,
        help="IP or hostname of the Mac mini",
    )
    parser.add_argument(
        "--device-id",
        default=None,
        help="VmbPy device ID (e.g. DEV_1AB22C0B039D). Default: first camera.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=600,
        help="Seconds between captures (default: 600 = 10 minutes)",
    )
    parser.add_argument(
        "--dark-threshold",
        type=float,
        default=100000,
        help="Exposure (us) above which scene is considered too dark (default: 100000)",
    )
    parser.add_argument(
        "--light-threshold",
        type=float,
        default=50000,
        help="Exposure (us) below which scene is considered light again (default: 50000)",
    )
    parser.add_argument(
        "--dark-interval",
        type=int,
        default=1800,
        help="Seconds between light probes while in dark mode (default: 1800 = 30 min)",
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
    is_dark = False
    dark_probes = 0
    consecutive_failures = 0

    log.info(
        "Starting continuous capture: camera=%s, device=%s, interval=%ds, "
        "dark_threshold=%.0fus, light_threshold=%.0fus, dark_interval=%ds, mac=%s",
        camera_id,
        args.device_id or "auto",
        args.interval,
        args.dark_threshold,
        args.light_threshold,
        args.dark_interval,
        args.mac_host,
    )

    while not _shutdown:
        try:
            frame, meta = _capture_frame(device_id=args.device_id)
            exposure = meta.get("exposure_us", 0)
            consecutive_failures = 0

            if is_dark:
                dark_probes += 1
                if exposure < args.light_threshold and np.mean(frame) >= BRIGHTNESS_FLOOR:
                    is_dark = False
                    log.info(
                        "Daylight resumed: exposure=%.0fus < threshold=%.0fus "
                        "(after %d dark probes)",
                        exposure, args.light_threshold, dark_probes,
                    )
                    dark_probes = 0
                    result = _encode_and_send(
                        frame, meta, camera_id, args.mac_host,
                    )
                    history.append(result)
                    if result["sent"]:
                        total_sent += 1
                    else:
                        total_failed += 1
                    _print_status(
                        camera_id, history, total_sent, total_failed,
                        is_dark, args.dark_interval,
                    )
                else:
                    log.info(
                        "Dark probe %d: exposure=%.0fus, mean_px=%.1f "
                        "(need exposure < %.0fus to resume)",
                        dark_probes, exposure, float(np.mean(frame)),
                        args.light_threshold,
                    )
            else:
                if _is_too_dark(frame, meta, args.dark_threshold):
                    is_dark = True
                    dark_probes = 0
                    log.info(
                        "Dark mode entered: exposure=%.0fus > threshold=%.0fus, "
                        "mean_px=%.1f",
                        exposure, args.dark_threshold, float(np.mean(frame)),
                    )
                else:
                    result = _encode_and_send(
                        frame, meta, camera_id, args.mac_host,
                    )
                    history.append(result)
                    if result["sent"]:
                        total_sent += 1
                    else:
                        total_failed += 1
                    _print_status(
                        camera_id, history, total_sent, total_failed,
                        is_dark, args.dark_interval,
                    )

        except Exception as exc:
            consecutive_failures += 1
            total_failed += 1
            if is_dark:
                dark_probes += 1
                log.warning(
                    "Dark probe %d failed (camera unresponsive): %s",
                    dark_probes, exc,
                )
            else:
                log.error("Capture cycle failed: %s", exc)
            if consecutive_failures >= 5:
                log.error(
                    "Camera has failed %d consecutive times -- "
                    "check USB connection and power cycle if needed",
                    consecutive_failures,
                )

        if _shutdown:
            break

        sleep_time = args.dark_interval if is_dark else args.interval
        if is_dark:
            log.info(
                "Dark mode: next light probe in %ds (%dm)...",
                sleep_time, sleep_time // 60,
            )
        else:
            log.info(
                "Next capture in %d seconds (Ctrl+C to stop)...", sleep_time,
            )
        for _ in range(sleep_time):
            if _shutdown:
                break
            time.sleep(1)

    log.info(
        "Stopped. Total frames: %d (sent: %d, buffered: %d), dark probes: %d",
        len(history),
        total_sent,
        total_failed,
        dark_probes,
    )


if __name__ == "__main__":
    main()
