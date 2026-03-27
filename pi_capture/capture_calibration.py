"""Headless calibration capture for dark frame and flat field.

Run once per camera at deployment time. No GUI required.

Camera ID is auto-detected from hostname: node1 -> cam01, node2 -> cam02, etc.
Can be overridden with --camera-id.

Usage:
    python pi_capture/capture_calibration.py --mode dark --mac-host 192.168.178.25
    python pi_capture/capture_calibration.py --mode flat --mac-host 192.168.178.25

Dark frame: cap the lens (or capture in complete darkness). Averages 16
frames at minimum exposure / zero gain to measure sensor noise baseline.

Flat field: point the camera at a uniformly illuminated white surface
(overcast sky or white card with even lighting). Averages 16 frames,
dark-subtracts if a dark frame is available, and normalizes so mean = 1.0.

After capture, if --mac-host is provided, the .npy file is automatically
SCPed to the Mac's calibration/ directory.

Environment:
    GENICAM_GENTL64_PATH must point to the Vimba X GenTL directory.
"""

import argparse
import logging
import socket
import subprocess
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger("capture_calibration")

FRAME_COUNT = 16
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALIBRATION_DIR = PROJECT_ROOT / "calibration"

REMOTE_CAL_DIR = "/Users/swc/Desktop/SWC/software/calibration"
SSH_USER = "swc"


def _camera_id_from_hostname() -> str:
    """Derive camera ID from hostname: node1 -> cam01, node2 -> cam02, etc."""
    hostname = socket.gethostname().lower()
    for i in range(1, 100):
        if f"node{i}" in hostname:
            return f"cam{i:02d}"
    return "cam01"


def _frame_to_numpy(frame, cam_width: int, cam_height: int) -> np.ndarray:
    """Extract a 2-D uint8 array from a VmbPy Frame, with multiple fallbacks.

    cam_width / cam_height come from the camera's Width/Height features
    and are used when the frame object lacks its own dimension metadata.
    """
    try:
        return frame.as_numpy_ndarray().squeeze().copy()
    except Exception:
        pass

    try:
        import vmbpy
        frame.convert_pixel_format(vmbpy.PixelFormat.Mono8)
        return frame.as_numpy_ndarray().squeeze().copy()
    except Exception:
        pass

    buf = frame.get_buffer()
    w = cam_width
    h = cam_height
    expected = w * h
    buf_len = len(buf)
    if buf_len >= expected * 2:
        arr = np.array(buf[:expected * 2], dtype=np.uint8).view(np.uint16)
        arr = (arr >> 2).astype(np.uint8)
    else:
        arr = np.array(buf[:expected], dtype=np.uint8)
    return arr.reshape(h, w).copy()


def _capture_frames(
    n: int, min_exposure: bool = False, device_id: str | None = None
) -> list:
    """Capture n frames from the camera. Returns list of numpy arrays."""
    import vmbpy

    frames = []
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

        log.info("Using camera: %s", cam_obj.get_id())
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

            if min_exposure:
                try:
                    cam.get_feature_by_name("ExposureAuto").set("Off")
                    exp_feat = cam.get_feature_by_name("ExposureTime")
                    exp_feat.set(exp_feat.get_range()[0])
                except Exception as exc:
                    log.warning("Could not set minimum exposure: %s", exc)
                try:
                    gain_feat = cam.get_feature_by_name("Gain")
                    gain_feat.set(gain_feat.get_range()[0])
                except Exception as exc:
                    log.warning("Could not set minimum gain: %s", exc)
            else:
                try:
                    cam.get_feature_by_name("ExposureAuto").set("Once")
                except Exception:
                    pass

            for i in range(n):
                frame = cam.get_frame(timeout_ms=5000)
                arr = _frame_to_numpy(frame, cam_w, cam_h)
                frames.append(arr)
                if (i + 1) % 4 == 0:
                    log.info("Captured %d / %d", i + 1, n)

    return frames


def capture_dark(output: Path, device_id: str | None = None) -> None:
    """Capture and average dark frames (lens capped, minimum exposure)."""
    log.info("Capturing %d dark frames (lens must be capped)...", FRAME_COUNT)
    frames = _capture_frames(FRAME_COUNT, min_exposure=True, device_id=device_id)

    accumulator = frames[0].astype(np.float32)
    for f in frames[1:]:
        accumulator += f.astype(np.float32)
    dark = accumulator / len(frames)

    output.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(output), dark)
    log.info(
        "Dark frame saved: %s (mean=%.2f, shape=%s)",
        output,
        dark.mean(),
        dark.shape,
    )


def capture_flat(
    output: Path,
    dark_path: Path | None = None,
    device_id: str | None = None,
) -> None:
    """Capture and average flat field frames (uniform white surface)."""
    log.info(
        "Capturing %d flat field frames (point at uniform surface)...",
        FRAME_COUNT,
    )
    frames = _capture_frames(FRAME_COUNT, min_exposure=False, device_id=device_id)

    accumulator = frames[0].astype(np.float32)
    for f in frames[1:]:
        accumulator += f.astype(np.float32)
    flat = accumulator / len(frames)

    if dark_path and dark_path.exists():
        dark = np.load(str(dark_path)).astype(np.float32)
        if dark.shape == flat.shape:
            flat = flat - dark
            np.clip(flat, 1.0, None, out=flat)
            log.info("Subtracted dark frame from flat field")
        else:
            log.warning("Dark shape mismatch, skipping subtraction")

    mean_val = flat.mean()
    if mean_val > 0:
        flat = flat / mean_val

    output.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(output), flat)
    log.info(
        "Flat field saved: %s (mean=%.4f, shape=%s)",
        output,
        flat.mean(),
        flat.shape,
    )


def _send_calibration_to_mac(npy_path: Path, mac_host: str) -> bool:
    """SCP calibration .npy file to the Mac."""
    subprocess.run(
        ["ssh", f"{SSH_USER}@{mac_host}", "mkdir", "-p", REMOTE_CAL_DIR],
        check=False,
        timeout=15,
    )
    result = subprocess.run(
        ["scp", str(npy_path), f"{SSH_USER}@{mac_host}:{REMOTE_CAL_DIR}/"],
        check=False,
        timeout=60,
    )
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Headless calibration capture (dark frame / flat field)"
    )
    parser.add_argument(
        "--camera-id",
        default=None,
        help="Camera identifier (cam01, cam02). Default: auto from hostname "
             "(node1 -> cam01, node2 -> cam02, ...)",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["dark", "flat"],
        help="Calibration type: dark (lens capped) or flat (uniform surface)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output .npy path. Default: calibration/<camera-id>_<mode>.npy",
    )
    parser.add_argument(
        "--dark-path",
        default=None,
        help="(flat mode only) Path to dark frame .npy for subtraction",
    )
    parser.add_argument(
        "--device-id",
        default=None,
        help="VmbPy device ID (e.g. DEV_1AB22C0B039D). Default: first camera.",
    )
    parser.add_argument(
        "--mac-host",
        default=None,
        help="IP or hostname of the Mac. If set, SCP calibration files after capture.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    camera_id = args.camera_id or _camera_id_from_hostname()
    log.info("Camera ID: %s (hostname: %s)", camera_id, socket.gethostname())

    if args.output:
        output = Path(args.output)
    else:
        output = CALIBRATION_DIR / f"{camera_id}_{args.mode}.npy"

    if args.mode == "dark":
        capture_dark(output, device_id=args.device_id)
    else:
        dark_path = None
        if args.dark_path:
            dark_path = Path(args.dark_path)
        else:
            default_dark = CALIBRATION_DIR / f"{camera_id}_dark.npy"
            if default_dark.exists():
                dark_path = default_dark
                log.info("Using existing dark frame: %s", dark_path)

        capture_flat(output, dark_path, device_id=args.device_id)

    if args.mac_host:
        log.info("Sending %s to Mac (%s)...", output.name, args.mac_host)
        if _send_calibration_to_mac(output, args.mac_host):
            log.info("Calibration file sent to Mac successfully")
        else:
            log.error("Failed to send calibration file to Mac")
    else:
        log.info("No --mac-host specified, skipping SCP to Mac")


if __name__ == "__main__":
    main()
