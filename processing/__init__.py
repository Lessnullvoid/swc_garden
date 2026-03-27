"""
Processing package for spectral radiance and vegetation analysis.

Modules:
    calibration           -- Radiometric calibration (dark frame, flat field, DN-to-radiance)
    vegetation            -- Vegetation indices (NDVI, GNDVI, SAVI) and band registration
    analysis              -- Live broadband analysis (histogram, reflectance map, change detection)
    digital_filters       -- Simulate Red/NIR bands from broadband (no physical filters)
    spectral_calibration  -- QE(λ) from known-wavelength illumination (software, no camera filter)
    spectral_radiance     -- Spectral radiance L(λ) from Red/NIR (two-point or basis unmixing)
"""

from .calibration import RadiometricCalibrator
from .digital_filters import (
    simulate_red_nir_synthetic,
    simulate_red_nir_dual_exposure,
    digital_filter_calibration_weights,
    effective_weights_from_qe_calibration,
)
from .spectral_calibration import (
    build_qe_from_illumination,
    effective_band_weights,
    save_qe_calibration,
    load_qe_calibration,
    interpolate_qe_to_grid,
)
from .spectral_radiance import (
    get_default_basis_spectra,
    spectral_radiance_from_unmixing,
    two_point_spectral_radiance,
    spectrum_at_wavelength,
    spectrum_to_rgb_display,
    DEFAULT_WAVELENGTH_GRID_NM,
)
from .vegetation import (
    compute_ndvi,
    compute_gndvi,
    compute_savi,
    compute_evi,
    ndvi_colormap,
    index_colormap,
    align_bands,
    VEGETATION_INDICES,
)
from .analysis import (
    render_histogram,
    reflectance_colormap,
    ChangeDetector,
    compute_statistics,
)
