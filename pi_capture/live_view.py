"""MJPEG HTTP live view for the Alvium camera on Raspberry Pi.

Streams camera frames as MJPEG over HTTP so you can view the feed in a
browser on another machine (e.g. Mac) for positioning and calibration.

Usage:
    python pi_capture/live_view.py
    python pi_capture/live_view.py --camera-id DEV_1AB22C0B039D --port 8080

Then open http://<pi-ip>:8080 in a browser.

Environment:
    GENICAM_GENTL64_PATH must point to the Vimba X ARM GenTL directory.
"""

import argparse
import logging
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np

log = logging.getLogger("live_view")

JPEG_QUALITY = 70
BOUNDARY = b"--frameboundary"

HTML_PAGE = """\
<!DOCTYPE html>
<html>
<head>
<title>Pi Camera Live View</title>
<style>
  body {{
    margin: 0;
    background: #1a1a1a;
    display: flex;
    flex-direction: column;
    align-items: center;
    font-family: system-ui, sans-serif;
    color: #ccc;
  }}
  h1 {{
    margin: 16px 0 8px;
    font-size: 18px;
    font-weight: 500;
  }}
  img {{
    max-width: 100vw;
    max-height: 90vh;
    border: 1px solid #333;
  }}
  p {{
    margin: 8px 0;
    font-size: 13px;
    color: #888;
  }}
</style>
</head>
<body>
  <h1>Pi Camera Live View</h1>
  <img src="/stream" alt="camera feed" />
  <p>{width}x{height} | JPEG q{quality} | Ctrl+C on Pi to stop</p>
</body>
</html>
"""


class FrameBuffer:
    """Thread-safe single-frame buffer updated by the capture thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._width = 0
        self._height = 0

    def update(self, jpeg: bytes, width: int, height: int) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._width = width
            self._height = height

    def get(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    @property
    def width(self) -> int:
        with self._lock:
            return self._width

    @property
    def height(self) -> int:
        with self._lock:
            return self._height


frame_buffer = FrameBuffer()

_extract_method = None


def _frame_to_numpy(frame, cam_width: int, cam_height: int) -> np.ndarray | None:
    """Extract a 2-D uint8 array from a VmbPy Frame.

    Auto-detects which extraction method works on the first call,
    then uses only that method for subsequent frames to avoid
    exception overhead.  Returns None if extraction fails.
    """
    global _extract_method

    if _extract_method == "native":
        try:
            return frame.as_numpy_ndarray().squeeze().copy()
        except Exception:
            _extract_method = None

    if _extract_method == "raw":
        return _raw_buffer(frame, cam_width, cam_height)

    try:
        arr = frame.as_numpy_ndarray().squeeze().copy()
        _extract_method = "native"
        log.info("Frame extraction: using native as_numpy_ndarray")
        return arr
    except Exception:
        pass

    try:
        import vmbpy
        frame.convert_pixel_format(vmbpy.PixelFormat.Mono8)
        arr = frame.as_numpy_ndarray().squeeze().copy()
        _extract_method = "native"
        log.info("Frame extraction: using convert_pixel_format + native")
        return arr
    except Exception:
        pass

    arr = _raw_buffer(frame, cam_width, cam_height)
    if arr is not None:
        _extract_method = "raw"
        log.info("Frame extraction: using raw buffer (%d bytes for %dx%d)",
                 len(frame.get_buffer()), cam_width, cam_height)
    return arr


def _raw_buffer(frame, w: int, h: int) -> np.ndarray | None:
    """Read raw frame buffer and reshape to (h, w) uint8."""
    try:
        buf = frame.get_buffer()
        expected = w * h
        buf_len = len(buf)
        if buf_len < expected:
            return None
        if buf_len >= expected * 2:
            arr = np.array(buf[:expected * 2], dtype=np.uint8).view(np.uint16)
            arr = (arr >> 2).astype(np.uint8)
        else:
            arr = np.array(buf[:expected], dtype=np.uint8)
        return arr.reshape(h, w).copy()
    except Exception:
        return None


def capture_loop(camera_id: str | None, target_width: int | None) -> None:
    """Continuously capture frames and update the shared buffer."""
    import vmbpy

    with vmbpy.VmbSystem.get_instance() as vmb:
        cameras = vmb.get_all_cameras()
        if not cameras:
            log.error("No cameras detected")
            return

        cam_obj = None
        if camera_id:
            for c in cameras:
                if c.get_id() == camera_id:
                    cam_obj = c
                    break
            if cam_obj is None:
                log.error(
                    "Camera %s not found. Available: %s",
                    camera_id,
                    [c.get_id() for c in cameras],
                )
                return
        else:
            cam_obj = cameras[0]

        log.info("Opening camera: %s (%s)", cam_obj.get_id(), cam_obj.get_name())

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
                cam.get_feature_by_name("ExposureAuto").set("Continuous")
            except Exception:
                pass

            while not _shutdown.is_set():
                try:
                    frame = cam.get_frame(timeout_ms=2000)
                except Exception as exc:
                    log.warning("Frame grab failed: %s", exc)
                    time.sleep(0.1)
                    continue

                try:
                    status = frame.get_status()
                    if status != vmbpy.FrameStatus.Complete:
                        continue
                except Exception:
                    pass

                arr = _frame_to_numpy(frame, cam_w, cam_h)
                if arr is None:
                    continue
                h, w = arr.shape[:2]

                if target_width and w > target_width:
                    scale = target_width / w
                    arr = cv2.resize(
                        arr,
                        (target_width, int(h * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                    h, w = arr.shape[:2]

                ok, encoded = cv2.imencode(
                    ".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                )
                if ok:
                    frame_buffer.update(encoded.tobytes(), w, h)


_shutdown = threading.Event()


class StreamHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/stream":
            self._handle_stream()
        elif self.path == "/" or self.path == "/index.html":
            self._handle_index()
        else:
            self.send_error(404)

    def _handle_index(self):
        html = HTML_PAGE.format(
            width=frame_buffer.width or "?",
            height=frame_buffer.height or "?",
            quality=JPEG_QUALITY,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _handle_stream(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=frameboundary",
        )
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

        try:
            while not _shutdown.is_set():
                jpeg = frame_buffer.get()
                if jpeg is None:
                    time.sleep(0.05)
                    continue

                self.wfile.write(BOUNDARY + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(
                    f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                )
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                time.sleep(0.04)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        if "/stream" not in str(args):
            log.info(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MJPEG HTTP live view for Pi camera"
    )
    parser.add_argument(
        "--camera-id",
        default=None,
        help="VmbPy camera ID (default: first camera found)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP server port (default: 8080)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Downscale to this width for lower bandwidth (default: native)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    def _signal_handler(sig, frame):
        log.info("Shutting down...")
        _shutdown.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    capture_thread = threading.Thread(
        target=capture_loop,
        args=(args.camera_id, args.width),
        daemon=True,
    )
    capture_thread.start()

    time.sleep(0.5)

    server = HTTPServer(("0.0.0.0", args.port), StreamHandler)
    server.timeout = 1.0
    log.info("Serving on http://0.0.0.0:%d", args.port)
    log.info("Open http://<pi-ip>:%d in your browser", args.port)

    while not _shutdown.is_set():
        server.handle_request()

    server.server_close()
    log.info("Server stopped")


if __name__ == "__main__":
    main()
