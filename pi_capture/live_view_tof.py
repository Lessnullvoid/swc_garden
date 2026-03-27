"""MJPEG HTTP live view for the Arducam ToF depth camera on Raspberry Pi.

Streams a side-by-side composite (depth colormap | amplitude) as MJPEG
over HTTP so you can view the feed in a browser for positioning.
No display needed on the Pi.

Usage:
    python pi_capture/live_view_tof.py
    python pi_capture/live_view_tof.py --port 8080

Then open http://<pi-ip>:8080 in a browser.

Dependencies:
    pip install ArducamDepthCamera numpy opencv-python-headless
"""

import argparse
import logging
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np

log = logging.getLogger("live_view_tof")

JPEG_QUALITY = 75
BOUNDARY = b"--frameboundary"

HTML_PAGE = """\
<!DOCTYPE html>
<html>
<head>
<title>ToF Camera Live View</title>
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
  <h1>Arducam ToF -- Depth + Amplitude</h1>
  <img src="/stream" alt="camera feed" />
  <p>Left: depth colormap | Right: amplitude | Ctrl+C on Pi to stop</p>
</body>
</html>
"""


class FrameBuffer:
    """Thread-safe single-frame buffer updated by the capture thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jpeg = None
        self._width = 0
        self._height = 0

    def update(self, jpeg, width, height):
        with self._lock:
            self._jpeg = jpeg
            self._width = width
            self._height = height

    def get(self):
        with self._lock:
            return self._jpeg

    @property
    def width(self):
        with self._lock:
            return self._width

    @property
    def height(self):
        with self._lock:
            return self._height


frame_buffer = FrameBuffer()
_shutdown = threading.Event()


def _depth_to_colormap(depth, depth_range):
    """Convert float32 depth to a BGR colormap image."""
    if depth_range <= 0:
        depth_range = 4.0
    normalized = np.clip(depth / depth_range, 0.0, 1.0)
    gray = (normalized * 255).astype(np.uint8)
    return cv2.applyColorMap(gray, cv2.COLORMAP_JET)


def _amplitude_to_gray(amplitude):
    """Convert float32 amplitude to uint8 grayscale."""
    if amplitude.max() > 0:
        normalized = np.clip(amplitude / amplitude.max(), 0.0, 1.0)
    else:
        normalized = amplitude
    return (normalized * 255).astype(np.uint8)


def capture_loop():
    """Continuously capture ToF frames and update the shared buffer."""
    import ArducamDepthCamera as ac

    cam = ac.ArducamCamera()
    ret = cam.open(ac.TOFConnect.CSI, 0)
    if ret != 0:
        log.error("Failed to open Arducam ToF camera (error %d)", ret)
        return

    ret = cam.start(ac.TOFOutput.DEPTH)
    if ret != 0:
        log.error("Failed to start camera (error %d)", ret)
        cam.close()
        return

    depth_range = cam.getControl(ac.TOFControl.RANGE)
    info = cam.getCameraInfo()
    log.info(
        "ToF camera started: %dx%d, range=%.2fm",
        info.width, info.height, depth_range,
    )

    while not _shutdown.is_set():
        frame = cam.requestFrame(2000)
        if frame is None or not isinstance(frame, ac.DepthData):
            time.sleep(0.05)
            continue

        try:
            depth = frame.getDepthData()
            amplitude = frame.getAmplitudeData()
        except Exception as exc:
            log.warning("Frame data error: %s", exc)
            cam.releaseFrame(frame)
            continue

        cam.releaseFrame(frame)

        depth_color = _depth_to_colormap(depth, depth_range)
        amp_gray = _amplitude_to_gray(amplitude)
        amp_bgr = cv2.cvtColor(amp_gray, cv2.COLOR_GRAY2BGR)

        composite = np.hstack([depth_color, amp_bgr])
        h, w = composite.shape[:2]

        ok, encoded = cv2.imencode(
            ".jpg", composite, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if ok:
            frame_buffer.update(encoded.tobytes(), w, h)

    cam.stop()
    cam.close()
    log.info("ToF camera closed")


class StreamHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/stream":
            self._handle_stream()
        elif self.path in ("/", "/index.html"):
            self._handle_index()
        else:
            self.send_error(404)

    def _handle_index(self):
        html = HTML_PAGE.encode()
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


def main():
    parser = argparse.ArgumentParser(
        description="MJPEG HTTP live view for Arducam ToF camera"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP server port (default: 8080)",
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

    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()

    time.sleep(1.0)

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
