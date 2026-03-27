"""
Spectral (QE(λ)) calibration using software and known-wavelength illumination.

Constraint (no physical filters on camera):
------------------------------------------
You cannot recover QE(λ) from ordinary broadband images alone. One DN per pixel
is one integral over wavelength; inverting that to get QE(λ) is underdetermined.
So "digital" QE calibration means: no filter on the camera, but we control the
*illumination* and infer QE(λ) from the sensor response at each wavelength.

Workflow:
---------
1. Illuminate the sensor with *known wavelengths* (no filter on camera):
   - Monochromator, or
   - Set of narrow-band LEDs (e.g. 450, 530, 660, 850 nm), or
   - Tunable light source.
2. For each wavelength λ_i: capture image(s), record mean (or median) DN,
   exposure time, and gain. Optionally record known radiance at λ_i.
3. Compute relative response: R(λ) = DN(λ) / (exposure * gain_linear).
   If radiance L(λ) is known: QE(λ) ∝ R(λ) / L(λ). Otherwise use R(λ)
   as a relative spectral response and normalize to get a QE-like curve.
4. Save the (wavelength_nm, QE) curve and use it to compute effective
   Red and NIR weights for the digital filter (so pseudo-NDVI scaling
   matches the sensor).

With a calibrated QE(λ), we still have only one broadband channel per frame,
so we cannot separate Red and NIR from a single image. The curve is used to:
- Define sensor-specific weights for synthetic or dual-exposure NDVI.
- Report or correct for sensor spectral sensitivity in analysis.
"""

import numpy as np


def _linear_gain(gain_db):
    return 10.0 ** (gain_db / 20.0) if gain_db != 0 else 1.0


def build_qe_from_illumination(
    wavelength_nm,
    dn_response,
    exposure_us,
    gain_db=0.0,
    radiance=None,
):
    """Build a relative QE(λ) curve from illumination-based measurements.

    No filter on the camera: each measurement is taken with the sensor
    illuminated at a single (or narrow) known wavelength.

    Parameters
    ----------
    wavelength_nm : array-like (N,)
        Wavelengths in nm (e.g. [450, 530, 660, 850] for LED set).
    dn_response : array-like (N,)
        Mean (or median) DN at each wavelength. Can be from a single
        pixel, ROI mean, or full-frame mean.
    exposure_us : array-like (N,) or float
        Exposure time in microseconds for each measurement (or same for all).
    gain_db : array-like (N,) or float
        Gain in dB for each measurement (or same for all).
    radiance : array-like (N,) or None
        If provided, known spectral radiance at each wavelength (same units
        for all). Then QE(λ) = response(λ) / radiance(λ) (relative).

    Returns
    -------
    wavelength_nm : ndarray (N,)
        Same as input, as 1D array.
    qe_relative : ndarray (N,)
        Relative QE (or spectral response), normalized to max 1.0.
        Same units as 1/radiance if radiance given; arbitrary otherwise.
    """
    wl = np.atleast_1d(np.asarray(wavelength_nm, dtype=np.float64))
    dn = np.atleast_1d(np.asarray(dn_response, dtype=np.float64))
    exp = np.atleast_1d(np.asarray(exposure_us, dtype=np.float64))
    if exp.size == 1:
        exp = np.full_like(wl, exp.flat[0])
    gain = np.atleast_1d(np.asarray(gain_db, dtype=np.float64))
    if gain.size == 1:
        gain = np.full_like(wl, gain.flat[0])

    n = len(wl)
    if len(dn) != n or len(exp) != n or len(gain) != n:
        raise ValueError("wavelength_nm, dn_response, exposure_us, gain_db must have same length")

    exposure_s = np.maximum(exp * 1e-6, 1e-9)
    gain_lin = _linear_gain(gain)
    # Response proportional to DN / (exposure * gain)
    response = dn / (exposure_s * gain_lin)

    if radiance is not None:
        rad = np.atleast_1d(np.asarray(radiance, dtype=np.float64))
        if rad.size == 1:
            rad = np.full_like(wl, rad.flat[0])
        if len(rad) != n:
            raise ValueError("radiance must have same length as wavelength_nm")
        np.clip(rad, 1e-12, None, out=rad)
        qe = response / rad
    else:
        qe = response.copy()

    qe = np.maximum(qe, 0.0)
    qe_max = np.max(qe)
    if qe_max > 0:
        qe = qe / qe_max
    return wl, qe.astype(np.float32)


def bandpass_gaussian(lam_nm, center_nm, fwhm_nm):
    """Gaussian bandpass: peak 1 at center_nm, FWHM in nm."""
    sigma = fwhm_nm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    return np.exp(-0.5 * ((lam_nm - center_nm) / sigma) ** 2)


def effective_band_weights(
    wavelength_nm,
    qe_response,
    red_center_nm=660.0,
    red_fwhm_nm=80.0,
    nir_center_nm=850.0,
    nir_fwhm_nm=100.0,
):
    """Compute effective Red and NIR weights from a QE(λ) curve.

    Integrates QE(λ) * bandpass(λ) over the given wavelength array to get
    the relative sensitivity in a "Red" and "NIR" band. Use these weights
    in the digital filter (e.g. for scaling synthetic or dual-exposure
    channels) so that NDVI-like indices are consistent with sensor response.

    Parameters
    ----------
    wavelength_nm : ndarray (N,)
        Wavelengths at which QE is defined (from build_qe_from_illumination).
    qe_response : ndarray (N,)
        Relative QE or spectral response at those wavelengths.
    red_center_nm, red_fwhm_nm : float
        Red band: Gaussian center and FWHM (nm). Typical 660 nm, 80 nm.
    nir_center_nm, nir_fwhm_nm : float
        NIR band: Gaussian center and FWHM (nm). Typical 850 nm, 100 nm.

    Returns
    -------
    red_weight, nir_weight : float
        Weights such that effective_red = integral QE*red_bandpass,
        effective_nir = integral QE*nir_bandpass. Normalized so
        red_weight + nir_weight = 1.
    """
    wl = np.asarray(wavelength_nm, dtype=np.float64).ravel()
    qe = np.asarray(qe_response, dtype=np.float64).ravel()
    if wl.size != qe.size or wl.size < 2:
        raise ValueError("wavelength_nm and qe_response must have same length >= 2")

    red_bp = bandpass_gaussian(wl, red_center_nm, red_fwhm_nm)
    nir_bp = bandpass_gaussian(wl, nir_center_nm, nir_fwhm_nm)

    # Trapezoid integration
    red_int = np.trapz(qe * red_bp, wl)
    nir_int = np.trapz(qe * nir_bp, wl)
    red_int = max(red_int, 0.0)
    nir_int = max(nir_int, 0.0)

    total = red_int + nir_int
    if total <= 0:
        return 0.5, 0.5
    return (red_int / total), (nir_int / total)


def interpolate_qe_to_grid(wavelength_nm, qe_response, grid_nm=None):
    """Interpolate QE(λ) onto a regular wavelength grid.

    Parameters
    ----------
    wavelength_nm : ndarray
        Measured wavelengths.
    qe_response : ndarray
        QE at those wavelengths.
    grid_nm : ndarray or None
        Target wavelengths. If None, use 400–1000 nm step 10 nm.

    Returns
    -------
    grid_nm, qe_grid : ndarray
        Interpolated QE on the grid (linear interpolation).
    """
    wl = np.asarray(wavelength_nm).ravel()
    qe = np.asarray(qe_response).ravel()
    if grid_nm is None:
        grid_nm = np.linspace(400.0, 1000.0, 61)
    else:
        grid_nm = np.asarray(grid_nm).ravel()
    qe_grid = np.interp(grid_nm, wl, qe)
    return grid_nm, qe_grid.astype(np.float32)


def save_qe_calibration(path, wavelength_nm, qe_response, meta=None):
    """Save QE(λ) calibration to .npz.

    meta : dict or None
        Optional keys (e.g. 'sensor', 'date', 'notes') stored in the archive.
    """
    data = {
        "wavelength_nm": np.asarray(wavelength_nm),
        "qe_response": np.asarray(qe_response),
    }
    if meta:
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                data["meta_" + k] = v
    np.savez_compressed(path, **data)


def load_qe_calibration(path):
    """Load QE(λ) calibration from .npz.

    Returns
    -------
    wavelength_nm : ndarray
    qe_response : ndarray
    meta : dict
        Any meta_* keys in the file (keys stripped of 'meta_' prefix).
    """
    f = np.load(path)
    wl = np.asarray(f["wavelength_nm"])
    qe = np.asarray(f["qe_response"])
    meta = {}
    for key in f.files:
        if key.startswith("meta_"):
            meta[key[5:]] = f[key].item() if f[key].ndim == 0 else str(f[key])
    return wl, qe, meta
