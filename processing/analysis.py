"""
Live broadband analysis tools.

These work with the unfiltered camera (no bandpass filters required):
    - Intensity histogram rendering
    - False-colour reflectance mapping
    - Temporal change detection
    - Per-region statistics
"""

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------

def render_histogram(image, width=320, height=180, color=(0, 200, 100)):
    """Render an intensity histogram as a BGR image.

    Parameters
    ----------
    image : ndarray (uint8, single or multi-channel)
        Input image.  Only the first channel is used.
    width, height : int
        Output image dimensions.
    color : tuple
        BGR colour for the histogram bars.

    Returns
    -------
    ndarray (uint8, shape=(height, width, 3))
    """
    if image.ndim == 3:
        src = image[:, :, 0]
    else:
        src = image

    hist = cv2.calcHist([src], [0], None, [256], [0, 256]).flatten()
    max_val = hist.max()
    if max_val == 0:
        max_val = 1

    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    margin_bottom = 18
    plot_h = height - margin_bottom
    bin_w = width / 256.0

    for i in range(256):
        x1 = int(i * bin_w)
        x2 = int((i + 1) * bin_w)
        bar_h = int(hist[i] / max_val * plot_h)
        if bar_h > 0:
            cv2.rectangle(canvas,
                          (x1, plot_h - bar_h),
                          (x2, plot_h),
                          color, -1)

    # Axis labels
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(canvas, "0", (2, height - 3), font, 0.3,
                (160, 160, 160), 1, cv2.LINE_AA)
    cv2.putText(canvas, "255", (width - 28, height - 3), font, 0.3,
                (160, 160, 160), 1, cv2.LINE_AA)

    # Mean line
    mean_val = src.mean()
    mean_x = int(mean_val / 255.0 * width)
    cv2.line(canvas, (mean_x, 0), (mean_x, plot_h), (0, 150, 255), 1)
    cv2.putText(canvas, "u={:.0f}".format(mean_val),
                (min(mean_x + 4, width - 50), 12),
                font, 0.3, (0, 150, 255), 1, cv2.LINE_AA)

    return canvas


# ---------------------------------------------------------------------------
# Reflectance map (false-colour intensity)
# ---------------------------------------------------------------------------

def reflectance_colormap(image, colormap=cv2.COLORMAP_TURBO):
    """Map a grayscale image to a false-colour reflectance visualization.

    If the input is float32 (calibrated radiance), it is normalized
    to [0, 255] using percentile clipping for contrast.

    Parameters
    ----------
    image : ndarray
        Grayscale image (uint8 or float32).
    colormap : int
        OpenCV colormap constant.

    Returns
    -------
    ndarray (uint8, BGR)
    """
    if image.dtype == np.float32 or image.dtype == np.float64:
        p_low = np.percentile(image, 1)
        p_high = np.percentile(image, 99)
        if p_high > p_low:
            norm = (image - p_low) / (p_high - p_low) * 255.0
        else:
            norm = np.zeros_like(image)
        norm = np.clip(norm, 0, 255).astype(np.uint8)
    else:
        norm = image

    if norm.ndim == 3:
        norm = norm[:, :, 0]

    return cv2.applyColorMap(norm, colormap)


# ---------------------------------------------------------------------------
# Temporal change detection
# ---------------------------------------------------------------------------

class ChangeDetector:
    """Detects changes between a stored reference frame and live frames.

    Usage:
        detector = ChangeDetector()
        detector.set_reference(frame0)
        change_map = detector.compute(frame_n)
    """

    def __init__(self):
        self.reference = None

    def set_reference(self, frame):
        """Store the reference frame (float32 copy)."""
        self.reference = frame.astype(np.float32)

    @property
    def has_reference(self):
        return self.reference is not None

    def compute(self, current):
        """Return the absolute change map (float32).

        Values represent the absolute difference from the reference.
        """
        if self.reference is None:
            return np.zeros_like(current, dtype=np.float32)
        cur = current.astype(np.float32)
        # Handle shape mismatch (e.g. ROI change)
        if cur.shape != self.reference.shape:
            return np.zeros_like(cur, dtype=np.float32)
        return np.abs(cur - self.reference)

    def compute_display(self, current, colormap=cv2.COLORMAP_HOT):
        """Return a false-colour change map ready for display (uint8, BGR).

        Brighter regions indicate larger changes from the reference.
        """
        diff = self.compute(current)
        # Normalize with percentile clipping
        p99 = np.percentile(diff, 99)
        if p99 > 0:
            diff = diff / p99 * 255.0
        diff = np.clip(diff, 0, 255).astype(np.uint8)
        return cv2.applyColorMap(diff, colormap)

    def reset(self):
        self.reference = None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_statistics(image):
    """Compute basic statistics for an image or ROI.

    Returns a dict with: min, max, mean, std, median, total_pixels.
    """
    flat = image.astype(np.float64).ravel()
    return {
        "min": float(np.min(flat)),
        "max": float(np.max(flat)),
        "mean": float(np.mean(flat)),
        "std": float(np.std(flat)),
        "median": float(np.median(flat)),
        "total_pixels": int(flat.size),
    }
