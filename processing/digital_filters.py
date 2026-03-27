"""
Digital simulation of Red and NIR bands from broadband camera data.

Without physical bandpass filters (e.g. 660 nm Red, 850 nm NIR), this module
provides two strategies to approximate Red and NIR from the monochrome
broadband sensor (300-1100 nm):

1. Synthetic split (single frame)
   - One broadband image is split into two "bands" using tunable weights and
     an optional brightness-based prior (brighter -> more NIR-like, darker ->
     more Red-like). Produces a spatially varying pseudo-NDVI for visualization.
   - Not physically accurate; useful for testing and qualitative comparison.

2. Dual exposure (two frames)
   - Two frames at different exposures act as two effective bands. After
     exposure normalization, the short-exposure image tends to saturate
     in NIR-rich (bright) regions; the long-exposure image carries more
     linear mix of Red and NIR. With a simple mapping (configurable which
     frame is "red-like" vs "nir-like"), we get two channels for NDVI.
   - More defensible than single-frame synthetic; still approximate without
     known sensor QE curves.

Sensor context: AR0522 monochrome, QE ~84% at 529 nm, ~30% at 850 nm.

QE(λ) calibration in software (no camera filter):
    Use processing.spectral_calibration: illuminate the sensor with
    known wavelengths (monochromator or LED set), record DN and exposure
    per wavelength, then build_qe_from_illumination() and save_qe_calibration().
    Load with load_qe_calibration() and pass to effective_weights_from_qe_calibration()
    to get sensor-specific Red/NIR weights for the digital filter.
"""

import numpy as np


def simulate_red_nir_synthetic(
    broadband,
    red_weight=0.5,
    nir_weight=0.9,
    use_brightness_prior=True,
    brightness_power=1.0,
):
    """Simulate Red and NIR bands from a single broadband image.

    Uses a spatial prior: brighter pixels are treated as more NIR-dominated
    (e.g. vegetation), darker as more Red-dominated (e.g. soil, shadow).
    The two output bands are scaled versions of the input with this
    weighting so that (NIR - Red) and (NIR + Red) vary across the image,
    giving a non-constant pseudo-NDVI.

    Parameters
    ----------
    broadband : ndarray (float32 or uint8/16)
        Single-band broadband image.
    red_weight : float
        Base scale for the simulated Red band (0..1 typical).
    nir_weight : float
        Base scale for the simulated NIR band (0..1 typical).
    use_brightness_prior : bool
        If True, modulate Red/NIR by normalized brightness for spatial
        variation. If False, red_sim and nir_sim are uniform multiples
        of broadband (NDVI would be constant).
    brightness_power : float
        Exponent for brightness factor; >1 emphasizes contrast.

    Returns
    -------
    red_sim, nir_sim : ndarray (float32)
        Same shape as broadband; suitable for compute_ndvi(red_sim, nir_sim).
    """
    img = np.asarray(broadband, dtype=np.float32)
    if img.ndim == 3 and img.shape[2] == 1:
        img = np.squeeze(img, axis=2)

    if not use_brightness_prior:
        red_sim = img * red_weight
        nir_sim = img * nir_weight
        return red_sim, nir_sim

    # Normalized brightness in [0, 1]
    mn, mx = np.percentile(img, (1, 99))
    if mx <= mn:
        mn, mx = img.min(), img.max()
    if mx > mn:
        norm = (img - mn) / (mx - mn)
    else:
        norm = np.ones_like(img)
    norm = np.clip(norm, 0.0, 1.0)
    norm = np.power(norm, brightness_power)

    # Prior: brighter -> more NIR, darker -> more Red
    # red_sim = broadband * red_weight * (1 - norm) + small baseline
    # nir_sim = broadband * (nir_low + (nir_weight - nir_low) * norm)
    red_sim = img * (red_weight * (1.0 - norm) + 0.1)
    nir_sim = img * (0.2 + (nir_weight - 0.2) * norm)
    return red_sim, nir_sim


def simulate_red_nir_dual_exposure(
    frame_short,
    frame_long,
    exposure_short_us,
    exposure_long_us,
    short_as_nir=True,
    dark_subtract=None,
):
    """Simulate Red and NIR from two broadband frames at different exposures.

    Both frames are normalized by exposure time so they represent comparable
    effective radiance. Then one frame is used as the "Red-like" band and the
    other as the "NIR-like" band. Typically, the short-exposure image
    saturates in bright (often NIR-rich) areas, so it can be used as an
    NIR proxy; the long-exposure image remains more linear and can serve as
    Red proxy (or the mapping can be swapped).

    Parameters
    ----------
    frame_short : ndarray
        Broadband image at shorter exposure.
    frame_long : ndarray
        Broadband image at longer exposure.
    exposure_short_us : float
        Exposure time in microseconds for frame_short.
    exposure_long_us : float
        Exposure time in microseconds for frame_long.
    short_as_nir : bool
        If True, use short-exposure as NIR-like and long as Red-like.
        If False, swap (long -> NIR-like, short -> Red-like).
    dark_subtract : ndarray or None
        Optional dark frame (same shape) to subtract from both frames
        before exposure normalization.

    Returns
    -------
    red_sim, nir_sim : ndarray (float32)
        Exposure-normalized bands; same shape as inputs.
    """
    t_short = max(exposure_short_us * 1e-6, 1e-9)
    t_long = max(exposure_long_us * 1e-6, 1e-9)

    s = np.asarray(frame_short, dtype=np.float32)
    l = np.asarray(frame_long, dtype=np.float32)
    if s.ndim == 3 and s.shape[2] == 1:
        s = np.squeeze(s, axis=2)
    if l.ndim == 3 and l.shape[2] == 1:
        l = np.squeeze(l, axis=2)

    if dark_subtract is not None:
        d = np.asarray(dark_subtract, dtype=np.float32)
        if d.ndim == 3 and d.shape[2] == 1:
            d = np.squeeze(d, axis=2)
        s = np.maximum(s - d, 0.0)
        l = np.maximum(l - d, 0.0)

    # Radiance proportional to DN / exposure
    rad_short = s / t_short
    rad_long = l / t_long

    if short_as_nir:
        nir_sim = rad_short
        red_sim = rad_long
    else:
        nir_sim = rad_long
        red_sim = rad_short

    return red_sim, nir_sim


def digital_filter_calibration_weights(
    qe_red_band_approx=0.7,
    qe_nir_band_approx=0.3,
):
    """Return nominal Red/NIR weighting from approximate sensor QE at two bands.

    For a single broadband channel, the measured DN is proportional to
    integral of (QE(lam) * L(lam)). If we approximate QE as constant in
    [600,700] nm (Red) and [800,900] nm (NIR), we get two effective weights.
    These can be used to scale a synthetic or dual-exposure split so that
    the effective Red and NIR contributions match the sensor's relative
    sensitivity (e.g. for consistent NDVI scaling across cameras).

    Parameters
    ----------
    qe_red_band_approx : float
        Approximate QE in the Red band (e.g. 0.7 for AR0522 around 529 nm
        is high; use lower if you assume 660 nm).
    qe_nir_band_approx : float
        Approximate QE in the NIR band (e.g. 0.3 for 850 nm from project context).

    Returns
    -------
    red_weight, nir_weight : float
        Weights such that red_equiv = broadband * red_weight, etc., for
        a flat spectrum. Ratio nir/red = qe_nir_band_approx / qe_red_band_approx.
    """
    total = qe_red_band_approx + qe_nir_band_approx
    if total <= 0:
        return 0.5, 0.5
    red_weight = qe_red_band_approx / total
    nir_weight = qe_nir_band_approx / total
    return red_weight, nir_weight


def effective_weights_from_qe_calibration(
    path=None,
    wavelength_nm=None,
    qe_response=None,
    red_center_nm=660.0,
    red_fwhm_nm=80.0,
    nir_center_nm=850.0,
    nir_fwhm_nm=100.0,
):
    """Red/NIR weights from a software-calibrated QE(λ) curve.

    Use this when you have built QE(λ) via spectral_calibration (illumination
    at known wavelengths, no filter on camera). The weights are used to scale
    the digital Red/NIR simulation so it matches sensor sensitivity.

    Parameters
    ----------
    path : str or None
        If set, load (wavelength_nm, qe_response) from load_qe_calibration(path).
    wavelength_nm, qe_response : ndarray or None
        If path is None, use these arrays (e.g. from build_qe_from_illumination).
    red_center_nm, red_fwhm_nm, nir_center_nm, nir_fwhm_nm : float
        Band definitions for effective_band_weights.

    Returns
    -------
    red_weight, nir_weight : float
        Normalized weights (sum 1) for Red and NIR bands.
    """
    from .spectral_calibration import (
        load_qe_calibration,
        effective_band_weights,
    )
    if path is not None:
        wl, qe, _ = load_qe_calibration(path)
    elif wavelength_nm is not None and qe_response is not None:
        wl = wavelength_nm
        qe = qe_response
    else:
        return digital_filter_calibration_weights()
    return effective_band_weights(
        wl, qe,
        red_center_nm=red_center_nm,
        red_fwhm_nm=red_fwhm_nm,
        nir_center_nm=nir_center_nm,
        nir_fwhm_nm=nir_fwhm_nm,
    )
