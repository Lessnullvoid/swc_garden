"""
Radiometric calibration pipeline.

Converts raw Digital Numbers (DN) from the sensor into calibrated
radiance values using dark frame subtraction, flat field correction,
and exposure/gain normalization.

    L = (DN - dark) / (flat * t_exp * g) * K

Where:
    DN    = raw pixel value
    dark  = dark frame (sensor noise baseline)
    flat  = normalized flat field (vignetting + pixel non-uniformity)
    t_exp = exposure time in seconds
    g     = linear gain
    K     = calibration coefficient (user-defined, default 1.0)
"""

import os

import numpy as np


class RadiometricCalibrator:
    """Performs radiometric calibration on raw camera frames.

    Typical workflow:
        1. Capture dark frames (lens capped) -> set_dark_frame()
        2. Capture flat field frames (uniform illumination) -> set_flat_field()
        3. For each live frame, call calibrate() to get radiance values.
    """

    def __init__(self):
        self.dark_frame = None       # float32, same shape as sensor
        self.flat_field = None       # float32, normalized (mean = 1.0)
        self.calibration_coeff = 1.0
        self._dark_count = 0
        self._flat_count = 0

    # -- dark frame ---------------------------------------------------------

    def set_dark_frame(self, image):
        """Set the dark frame from a single capture.

        For best results, call accumulate_dark_frame() multiple times
        and then finalize_dark_frame().
        """
        self.dark_frame = image.astype(np.float32)
        self._dark_count = 1

    def accumulate_dark_frame(self, image):
        """Add a frame to the running dark-frame average."""
        img = image.astype(np.float32)
        if self.dark_frame is None:
            self.dark_frame = img
            self._dark_count = 1
        else:
            self._dark_count += 1
            # Running average: avoids large accumulator
            self.dark_frame += (img - self.dark_frame) / self._dark_count

    def finalize_dark_frame(self):
        """Called after all dark frames have been accumulated."""
        # dark_frame is already the running mean -- nothing extra needed
        pass

    @property
    def has_dark(self):
        return self.dark_frame is not None

    @property
    def dark_count(self):
        return self._dark_count

    # -- flat field ---------------------------------------------------------

    def set_flat_field(self, image):
        """Set the flat field from a single capture (auto-normalized)."""
        ff = image.astype(np.float32)
        if self.dark_frame is not None:
            ff = ff - self.dark_frame
            ff = np.clip(ff, 1.0, None)
        mean_val = ff.mean()
        if mean_val > 0:
            ff = ff / mean_val
        self.flat_field = ff
        self._flat_count = 1

    def accumulate_flat_field(self, image):
        """Add a frame to the running flat-field average."""
        img = image.astype(np.float32)
        if self.dark_frame is not None:
            img = img - self.dark_frame
            img = np.clip(img, 0, None)
        if self.flat_field is None:
            self._flat_accumulator = img.copy()
            self._flat_count = 1
        else:
            self._flat_count += 1
            self._flat_accumulator += img

    def finalize_flat_field(self):
        """Normalize the accumulated flat field so its mean equals 1.0."""
        if self._flat_count > 0 and hasattr(self, '_flat_accumulator'):
            ff = self._flat_accumulator / self._flat_count
            mean_val = ff.mean()
            if mean_val > 0:
                ff = ff / mean_val
            self.flat_field = ff
            del self._flat_accumulator

    @property
    def has_flat(self):
        return self.flat_field is not None

    @property
    def flat_count(self):
        return self._flat_count

    # -- calibration --------------------------------------------------------

    def calibrate(self, image, exposure_us=1000.0, gain_db=0.0):
        """Convert a raw DN image to calibrated radiance (float32).

        Parameters
        ----------
        image : ndarray
            Raw image from the camera (uint8 or uint16).
        exposure_us : float
            Exposure time in microseconds.
        gain_db : float
            Sensor gain in decibels.

        Returns
        -------
        ndarray (float32)
            Calibrated radiance image.
        """
        img = image.astype(np.float32)

        # 1. Dark subtraction
        if self.dark_frame is not None:
            img = img - self.dark_frame
            np.clip(img, 0, None, out=img)

        # 2. Flat field correction
        if self.flat_field is not None:
            # Protect against division by near-zero
            ff = np.where(self.flat_field > 0.01, self.flat_field, 1.0)
            img = img / ff

        # 3. Normalize by exposure time (seconds) and linear gain
        exposure_s = max(exposure_us * 1e-6, 1e-9)
        gain_linear = 10.0 ** (gain_db / 20.0) if gain_db != 0 else 1.0
        img = img / (exposure_s * gain_linear)

        # 4. Apply calibration coefficient
        img = img * self.calibration_coeff

        return img

    def calibrate_display(self, image, exposure_us=1000.0, gain_db=0.0):
        """Calibrate and normalize to uint8 for display purposes."""
        cal = self.calibrate(image, exposure_us, gain_db)
        # Normalize to 0-255 using percentile clipping for better contrast
        p_low = np.percentile(cal, 1)
        p_high = np.percentile(cal, 99)
        if p_high > p_low:
            cal = (cal - p_low) / (p_high - p_low) * 255.0
        cal = np.clip(cal, 0, 255).astype(np.uint8)
        return cal

    # -- persistence --------------------------------------------------------

    def save(self, path):
        """Save calibration data to a .npz file."""
        data = {"calibration_coeff": self.calibration_coeff}
        if self.dark_frame is not None:
            data["dark_frame"] = self.dark_frame
        if self.flat_field is not None:
            data["flat_field"] = self.flat_field
        np.savez_compressed(path, **data)

    def load(self, path):
        """Load calibration data from a .npz file."""
        data = np.load(path)
        if "dark_frame" in data:
            self.dark_frame = data["dark_frame"]
            self._dark_count = 1
        if "flat_field" in data:
            self.flat_field = data["flat_field"]
            self._flat_count = 1
        if "calibration_coeff" in data:
            self.calibration_coeff = float(data["calibration_coeff"])

    def reset(self):
        """Clear all calibration data."""
        self.dark_frame = None
        self.flat_field = None
        self.calibration_coeff = 1.0
        self._dark_count = 0
        self._flat_count = 0
