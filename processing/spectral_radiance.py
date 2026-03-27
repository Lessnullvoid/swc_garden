"""
Spectral radiance analysis from Red and NIR band data.

With only two bands (Red and NIR, from physical filters or digital simulation),
we cannot measure a full L(λ) spectrum. This module provides two approaches:

1. **Two-point spectral radiance**
   Treat the two band values as radiance at two wavelengths (e.g. 660 nm and
   850 nm). Output is a minimal "spectrum" with two points, optionally
   linearly interpolated to a wavelength grid for visualization.

2. **Basis unmixing (linear spectral model)**
   Assume the scene spectrum is a mixture of a few basis spectra (e.g.
   vegetation, bare soil): L(λ) = a * veg(λ) + b * soil(λ). Given the two
   band responses, solve for (a, b) per pixel, then reconstruct
   L(λ) on a full wavelength grid. The result is a physically plausible
   spectrum that matches the Red and NIR measurements.

Both produce a spectral radiance image: (height, width, n_wavelengths) that
can be visualised as false-colour at chosen wavelengths or as a per-pixel
curve in a region of interest.
"""

import numpy as np

# Default wavelength grid (nm), 400-1000 step 10
DEFAULT_WAVELENGTH_GRID_NM = np.linspace(400.0, 1000.0, 61, dtype=np.float64)

# Band definitions (nm)
DEFAULT_RED_CENTER_NM = 660.0
DEFAULT_RED_FWHM_NM = 80.0
DEFAULT_NIR_CENTER_NM = 850.0
DEFAULT_NIR_FWHM_NM = 100.0


def _gaussian_bandpass(lam_nm, center_nm, fwhm_nm):
    sigma = fwhm_nm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    return np.exp(-0.5 * ((lam_nm - center_nm) / sigma) ** 2)


def _default_vegetation_reflectance(wl_nm):
    """Simplified vegetation reflectance: low in red (chlorophyll), high in NIR."""
    wl = np.asarray(wl_nm, dtype=np.float64)
    # Red trough around 660, NIR plateau 780-1000
    red_val = 0.08 + 0.02 * np.exp(-((wl - 660) ** 2) / (2 * 50 ** 2))
    nir_val = 0.45 + 0.15 * (1.0 - np.exp(-(np.maximum(wl - 700, 0) / 150) ** 2))
    return np.where(wl < 700, red_val, nir_val).astype(np.float32)


def _default_soil_reflectance(wl_nm):
    """Simplified bare soil: flatter, slightly higher in red than vegetation."""
    wl = np.asarray(wl_nm, dtype=np.float64)
    # Gently increasing with wavelength
    return (0.15 + 0.15 * (wl - 400) / 600).astype(np.float32)


def get_default_basis_spectra(wavelength_nm=None):
    """Return default basis spectra (vegetation, soil) on the given grid.

    Returns
    -------
    wavelength_nm : ndarray
    basis_dict : dict
        Keys "vegetation", "soil". Values are 1D arrays (reflectance or
        relative radiance) on wavelength_nm.
    """
    if wavelength_nm is None:
        wavelength_nm = DEFAULT_WAVELENGTH_GRID_NM
    wl = np.asarray(wavelength_nm).ravel()
    return wl, {
        "vegetation": _default_vegetation_reflectance(wl),
        "soil": _default_soil_reflectance(wl),
    }


def _band_integrals(wavelength_nm, basis_spectra, qe_response=None,
                    red_center_nm=DEFAULT_RED_CENTER_NM, red_fwhm_nm=DEFAULT_RED_FWHM_NM,
                    nir_center_nm=DEFAULT_NIR_CENTER_NM, nir_fwhm_nm=DEFAULT_NIR_FWHM_NM):
    """Compute integral of (band_weight * basis) for each band and each basis.

    band_weight is QE(λ) * gaussian_bandpass(λ). If qe_response is None, use
    flat QE (1.0). Returns dict of (basis_name -> (I_red, I_nir)).
    """
    wl = np.asarray(wavelength_nm).ravel()
    n = len(wl)
    if qe_response is None:
        qe = np.ones(n, dtype=np.float64)
    else:
        qe = np.asarray(qe_response).ravel()
        if len(qe) != n:
            qe = np.interp(wl, np.linspace(wl.min(), wl.max(), len(qe)), qe)
    w_red = qe * _gaussian_bandpass(wl, red_center_nm, red_fwhm_nm)
    w_nir = qe * _gaussian_bandpass(wl, nir_center_nm, nir_fwhm_nm)

    out = {}
    for name, spec in basis_spectra.items():
        s = np.asarray(spec).ravel()
        if len(s) != n:
            s = np.interp(wl, np.linspace(wl.min(), wl.max(), len(s)), s)
        I_red = np.trapz(w_red * s, wl)
        I_nir = np.trapz(w_nir * s, wl)
        out[name] = (float(np.maximum(I_red, 0)), float(np.maximum(I_nir, 0)))
    return out


def unmix_two_bands(red_band, nir_band, band_integrals, basis_names=("vegetation", "soil")):
    """Solve for mixture coefficients (a, b) per pixel from Red and NIR responses.

    Model: R_red = a*I_red_veg + b*I_red_soil, R_nir = a*I_nir_veg + b*I_nir_soil.
    Solves the 2x2 linear system per pixel; coefficients are clipped to >= 0.

    Parameters
    ----------
    red_band, nir_band : ndarray (H, W), float
        Red and NIR band images (radiance or calibrated response).
    band_integrals : dict
        From _band_integrals(): basis_name -> (I_red, I_nir).
    basis_names : tuple of str
        Order of bases (e.g. ("vegetation", "soil")).

    Returns
    -------
    coefficients : ndarray (H, W, 2)
        coefficients[:,:,0] = a (first basis), coefficients[:,:,1] = b (second basis).
    """
    red_band = np.asarray(red_band, dtype=np.float64)
    nir_band = np.asarray(nir_band, dtype=np.float64)
    if red_band.shape != nir_band.shape:
        raise ValueError("red_band and nir_band must have the same shape")
    I_red_0, I_nir_0 = band_integrals[basis_names[0]]
    I_red_1, I_nir_1 = band_integrals[basis_names[1]]
    # [I_red_0 I_red_1] [a]   [R_red]
    # [I_nir_0 I_nir_1] [b] = [R_nir]
    det = I_red_0 * I_nir_1 - I_red_1 * I_nir_0
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)  # avoid div by zero
    a = (red_band * I_nir_1 - nir_band * I_red_1) / det
    b = (nir_band * I_red_0 - red_band * I_nir_0) / det
    a = np.maximum(a, 0.0)
    b = np.maximum(b, 0.0)
    return np.stack([a, b], axis=-1).astype(np.float32)


def reconstruct_spectrum(coefficients, wavelength_nm, basis_spectra, basis_names=("vegetation", "soil")):
    """Reconstruct L(λ) = a*veg(λ) + b*soil(λ) per pixel.

    Parameters
    ----------
    coefficients : ndarray (H, W, 2)
        From unmix_two_bands.
    wavelength_nm : ndarray (n_wl,)
    basis_spectra : dict
        basis_name -> 1D array on wavelength_nm.
    basis_names : tuple of str

    Returns
    -------
    spectrum : ndarray (H, W, n_wl), float32
        Spectral radiance (relative or reflectance units) per pixel.
    """
    wl = np.asarray(wavelength_nm).ravel()
    n_wl = len(wl)
    v = np.asarray(basis_spectra[basis_names[0]]).ravel()
    s = np.asarray(basis_spectra[basis_names[1]]).ravel()
    if len(v) != n_wl:
        v = np.interp(wl, np.linspace(wl.min(), wl.max(), len(v)), v)
    if len(s) != n_wl:
        s = np.interp(wl, np.linspace(wl.min(), wl.max(), len(s)), s)
    a = coefficients[:, :, 0:1]   # (H, W, 1)
    b = coefficients[:, :, 1:2]   # (H, W, 1)
    spectrum = a * v + b * s     # (H, W, n_wl) via broadcast
    return spectrum.astype(np.float32)


def spectral_radiance_from_unmixing(
    red_band,
    nir_band,
    wavelength_nm=None,
    basis_spectra=None,
    qe_response=None,
    red_center_nm=DEFAULT_RED_CENTER_NM,
    red_fwhm_nm=DEFAULT_RED_FWHM_NM,
    nir_center_nm=DEFAULT_NIR_CENTER_NM,
    nir_fwhm_nm=DEFAULT_NIR_FWHM_NM,
):
    """Reconstruct spectral radiance L(λ) per pixel by unmixing Red and NIR.

    Uses a two-basis linear model (vegetation + soil). Returns a 3D array
    (H, W, n_wavelengths) that can be visualised at chosen wavelengths or
    plotted as a curve per pixel/region.

    Parameters
    ----------
    red_band, nir_band : ndarray (H, W)
        Red and NIR band images (same units; radiance or calibrated DN).
    wavelength_nm : ndarray or None
        Wavelength grid for output. Default 400-1000 nm step 10.
    basis_spectra : dict or None
        basis_name -> 1D spectrum. If None, use default vegetation and soil.
    qe_response : ndarray or None
        Optional sensor QE(λ) on wavelength_nm for band integrals.
    red_center_nm, red_fwhm_nm, nir_center_nm, nir_fwhm_nm : float
        Band definitions.

    Returns
    -------
    wavelength_nm : ndarray (n_wl,)
    spectrum : ndarray (H, W, n_wl), float32
        Reconstructed L(λ) per pixel (relative units).
    coefficients : ndarray (H, W, 2), float32
        Mixture coefficients (vegetation, soil).
    """
    if wavelength_nm is None:
        wavelength_nm = DEFAULT_WAVELENGTH_GRID_NM
    wl = np.asarray(wavelength_nm).ravel()
    if basis_spectra is None:
        _, basis_spectra = get_default_basis_spectra(wl)
    basis_names = ("vegetation", "soil")
    band_integrals = _band_integrals(
        wl, basis_spectra, qe_response,
        red_center_nm, red_fwhm_nm, nir_center_nm, nir_fwhm_nm,
    )
    coefficients = unmix_two_bands(red_band, nir_band, band_integrals, basis_names)
    spectrum = reconstruct_spectrum(coefficients, wl, basis_spectra, basis_names)
    return wl, spectrum, coefficients


def two_point_spectral_radiance(
    red_band,
    nir_band,
    wavelength_red_nm=DEFAULT_RED_CENTER_NM,
    wavelength_nir_nm=DEFAULT_NIR_CENTER_NM,
    interpolate_to_grid=True,
    wavelength_grid_nm=None,
):
    """Minimal spectral radiance: L(λ) at two wavelengths only.

    Treats the Red and NIR band values as radiance at 660 nm and 850 nm.
    Optionally interpolates linearly to a full wavelength grid for display.

    Parameters
    ----------
    red_band, nir_band : ndarray (H, W)
        Red and NIR band images.
    wavelength_red_nm, wavelength_nir_nm : float
        Wavelengths assigned to the two bands.
    interpolate_to_grid : bool
        If True, output is (H, W, len(wavelength_grid_nm)) with linear
        interpolation between the two points. If False, output is (H, W, 2).
    wavelength_grid_nm : ndarray or None
        Used only if interpolate_to_grid is True. Default 400-1000 step 10.

    Returns
    -------
    wavelength_nm : ndarray
        Either [wavelength_red_nm, wavelength_nir_nm] or wavelength_grid_nm.
    spectrum : ndarray (H, W, n_wl), float32
        Spectral radiance (two points or interpolated).
    """
    red_band = np.asarray(red_band, dtype=np.float32)
    nir_band = np.asarray(nir_band, dtype=np.float32)
    if not interpolate_to_grid:
        wl = np.array([wavelength_red_nm, wavelength_nir_nm], dtype=np.float32)
        spec = np.stack([red_band, nir_band], axis=-1)
        return wl, spec
    if wavelength_grid_nm is None:
        wavelength_grid_nm = DEFAULT_WAVELENGTH_GRID_NM
    wl = np.asarray(wavelength_grid_nm).ravel()
    # Linear interpolation: at wl_red use red_band, at wl_nir use nir_band
    t = (wl - wavelength_red_nm) / (wavelength_nir_nm - wavelength_red_nm) if wavelength_nir_nm != wavelength_red_nm else np.zeros_like(wl)
    t = np.clip(t, 0.0, 1.0)
    # (n_wl,) -> (1,1,n_wl); red_band (H,W,1), nir_band (H,W,1)
    spec = red_band[:, :, np.newaxis] * (1.0 - t) + nir_band[:, :, np.newaxis] * t
    return wl, spec.astype(np.float32)


def spectrum_at_wavelength(spectrum_image, wavelength_nm, target_nm):
    """Extract the band at the wavelength closest to target_nm.

    spectrum_image : (H, W, n_wl)
    wavelength_nm : (n_wl,)
    target_nm : float

    Returns
    -------
    band : ndarray (H, W)
    """
    idx = np.argmin(np.abs(np.asarray(wavelength_nm) - target_nm))
    return spectrum_image[:, :, idx].copy()


def spectrum_to_rgb_display(spectrum_image, wavelength_nm, r_nm=650, g_nm=550, b_nm=450):
    """Map a spectral radiance image to a false-colour RGB for display.

    Uses the bands closest to r_nm, g_nm, b_nm and normalizes to 0-255.
    If the spectrum does not extend to b_nm (e.g. we only have Red and NIR),
    blue can be set to a wavelength we have (e.g. 660) for a two-channel look.
    """
    r_band = spectrum_at_wavelength(spectrum_image, wavelength_nm, r_nm)
    g_band = spectrum_at_wavelength(spectrum_image, wavelength_nm, g_nm)
    b_band = spectrum_at_wavelength(spectrum_image, wavelength_nm, b_nm)
    rgb = np.stack([r_band, g_band, b_band], axis=-1)
    p1, p99 = np.percentile(rgb, (1, 99))
    if p99 > p1:
        rgb = (rgb - p1) / (p99 - p1) * 255.0
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return rgb
