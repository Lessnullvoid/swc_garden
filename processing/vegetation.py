"""
Vegetation index computation and band registration.

Supported indices:
    NDVI  -- Normalized Difference Vegetation Index
    GNDVI -- Green NDVI (uses green band instead of red)
    SAVI  -- Soil-Adjusted Vegetation Index
    EVI   -- Enhanced Vegetation Index (simplified two-band version)

All index functions accept float32 arrays and return values in [-1, 1]
(or the natural range of the index).

Band registration uses feature-based alignment (ORB + homography) to
compensate for camera movement between sequential filter captures.
"""

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Vegetation indices
# ---------------------------------------------------------------------------

VEGETATION_INDICES = {
    "NDVI": {
        "func": "compute_ndvi",
        "bands": ("NIR", "Red"),
        "range": (-1.0, 1.0),
        "description": "Normalized Difference Vegetation Index",
    },
    "GNDVI": {
        "func": "compute_gndvi",
        "bands": ("NIR", "Green"),
        "range": (-1.0, 1.0),
        "description": "Green NDVI -- sensitive to chlorophyll concentration",
    },
    "SAVI": {
        "func": "compute_savi",
        "bands": ("NIR", "Red"),
        "range": (-1.5, 1.5),
        "description": "Soil-Adjusted Vegetation Index (L=0.5)",
    },
    "EVI": {
        "func": "compute_evi",
        "bands": ("NIR", "Red"),
        "range": (-1.0, 1.0),
        "description": "Enhanced Vegetation Index (two-band simplified)",
    },
}


def _safe_divide(numerator, denominator):
    """Element-wise division, returning 0 where denominator is ~0."""
    return np.where(np.abs(denominator) > 1e-6,
                    numerator / denominator, 0.0)


def compute_ndvi(nir, red):
    """NDVI = (NIR - Red) / (NIR + Red)

    Parameters
    ----------
    nir, red : ndarray (float32)
        Single-band images (same shape).

    Returns
    -------
    ndarray (float32), values in [-1, 1].
    """
    nir = nir.astype(np.float32)
    red = red.astype(np.float32)
    ndvi = _safe_divide(nir - red, nir + red)
    return np.clip(ndvi, -1.0, 1.0)


def compute_gndvi(nir, green):
    """GNDVI = (NIR - Green) / (NIR + Green)

    More sensitive to chlorophyll concentration than standard NDVI.
    """
    nir = nir.astype(np.float32)
    green = green.astype(np.float32)
    gndvi = _safe_divide(nir - green, nir + green)
    return np.clip(gndvi, -1.0, 1.0)


def compute_savi(nir, red, soil_factor=0.5):
    """SAVI = ((NIR - Red) / (NIR + Red + L)) * (1 + L)

    Minimizes soil brightness influences.  L = 0.5 is standard for
    moderate vegetation cover.
    """
    nir = nir.astype(np.float32)
    red = red.astype(np.float32)
    L = soil_factor
    savi = _safe_divide(nir - red, nir + red + L) * (1.0 + L)
    return savi


def compute_evi(nir, red, gain_factor=2.5, soil_coeff=1.0):
    """Simplified two-band EVI = G * (NIR - Red) / (NIR + C * Red + 1)

    The full EVI uses a blue band for atmospheric correction; this
    simplified version omits it (suitable for ground-level imaging).
    """
    nir = nir.astype(np.float32)
    red = red.astype(np.float32)
    G = gain_factor
    C = soil_coeff
    evi = G * _safe_divide(nir - red, nir + C * red + 1.0)
    return np.clip(evi, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Colormaps
# ---------------------------------------------------------------------------

def _build_ndvi_lut():
    """Build a 256-entry BGR LUT for NDVI visualization.

    Colour scheme (common in remote sensing):
        -1.0 .. -0.1  water / non-vegetation  -> blue
        -0.1 ..  0.1  bare soil / rock        -> brown
         0.1 ..  0.3  sparse vegetation       -> yellow
         0.3 ..  0.6  moderate vegetation     -> light green
         0.6 ..  1.0  dense vegetation        -> dark green
    """
    lut = np.zeros((256, 1, 3), dtype=np.uint8)

    for i in range(256):
        # Map 0..255 to -1..1
        v = (i / 255.0) * 2.0 - 1.0

        if v < -0.1:
            # Water / non-vegetation -> blue
            r, g, b = 50, 60, 150
        elif v < 0.1:
            # Bare soil -> brown
            t = (v + 0.1) / 0.2
            r = int(140 + t * 40)
            g = int(100 + t * 40)
            b = int(60 + t * 10)
        elif v < 0.3:
            # Sparse -> yellow to light green
            t = (v - 0.1) / 0.2
            r = int(180 - t * 80)
            g = int(180 + t * 40)
            b = int(50 - t * 20)
        elif v < 0.6:
            # Moderate -> light green to green
            t = (v - 0.3) / 0.3
            r = int(100 - t * 70)
            g = int(220 - t * 40)
            b = int(30 + t * 10)
        else:
            # Dense -> dark green
            t = (v - 0.6) / 0.4
            r = int(30 - t * 20)
            g = int(180 - t * 60)
            b = int(40 - t * 20)

        lut[i, 0] = [b, g, r]  # BGR order for OpenCV

    return lut


_NDVI_LUT = None


def ndvi_colormap(index_image):
    """Apply the NDVI false-color map to an index image in [-1, 1].

    Returns a BGR uint8 image.
    """
    global _NDVI_LUT
    if _NDVI_LUT is None:
        _NDVI_LUT = _build_ndvi_lut()

    # Map [-1, 1] -> [0, 255]
    normalized = ((index_image + 1.0) / 2.0 * 255.0)
    normalized = np.clip(normalized, 0, 255).astype(np.uint8)
    colored = cv2.LUT(cv2.merge([normalized, normalized, normalized]),
                       _NDVI_LUT)
    return colored


def index_colormap(index_image, vmin=-1.0, vmax=1.0,
                   colormap=cv2.COLORMAP_JET):
    """Apply a generic OpenCV colormap to an index image.

    Parameters
    ----------
    index_image : ndarray (float32)
    vmin, vmax : float
        Value range to map onto [0, 255].
    colormap : int
        OpenCV colormap constant (e.g. cv2.COLORMAP_JET).

    Returns
    -------
    ndarray (uint8, BGR)
    """
    span = vmax - vmin if vmax != vmin else 1.0
    normalized = ((index_image - vmin) / span * 255.0)
    normalized = np.clip(normalized, 0, 255).astype(np.uint8)
    return cv2.applyColorMap(normalized, colormap)


# ---------------------------------------------------------------------------
# Band registration (image alignment)
# ---------------------------------------------------------------------------

def align_bands(reference, moving, max_features=5000):
    """Align *moving* image to *reference* using ORB feature matching.

    Use this when the camera may have shifted between sequential
    bandpass-filter captures.

    Parameters
    ----------
    reference : ndarray (uint8, single channel)
        The band image used as the fixed reference.
    moving : ndarray (uint8, single channel)
        The band image to be warped.
    max_features : int
        Maximum ORB features to detect.

    Returns
    -------
    aligned : ndarray
        The *moving* image warped to match *reference*.
    homography : ndarray (3x3) or None
        The computed homography matrix (None if alignment failed).
    """
    orb = cv2.ORB_create(nfeatures=max_features)
    kp1, des1 = orb.detectAndCompute(reference, None)
    kp2, des2 = orb.detectAndCompute(moving, None)

    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        return moving, None

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches = bf.knnMatch(des1, des2, k=2)

    # Lowe's ratio test
    good = []
    for pair in matches:
        if len(pair) == 2:
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good.append(m)

    if len(good) < 10:
        return moving, None

    pts1 = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts2 = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    H, mask = cv2.findHomography(pts2, pts1, cv2.RANSAC, 5.0)
    if H is None:
        return moving, None

    h, w = reference.shape[:2]
    aligned = cv2.warpPerspective(moving, H, (w, h))
    return aligned, H
