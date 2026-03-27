
"""
Allied Vision Alvium 1800 U-501m NIR - Camera Control Application

PyQt5-based GUI with real-time camera controls, radiometric calibration,
live broadband analysis, and vegetation index computation.

Usage:
    python3 camera_app.py              # Launch GUI
    python3 camera_app.py --list       # List connected cameras and exit
    python3 camera_app.py --camera-id "DEV_XXX"  # Use a specific camera
    python3 camera_app.py --low-memory # Slower refresh for Raspberry Pi / low-RAM
"""

import argparse
import math
import os
import sys
import threading
import time
from datetime import datetime

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import vmbpy

from processing import (
    RadiometricCalibrator,
    render_histogram,
    reflectance_colormap,
    ChangeDetector,
    compute_statistics,
    compute_ndvi,
    compute_savi,
    compute_evi,
    ndvi_colormap,
    index_colormap,
    align_bands,
    VEGETATION_INDICES,
    simulate_red_nir_synthetic,
    simulate_red_nir_dual_exposure,
    spectral_radiance_from_unmixing,
    two_point_spectral_radiance,
    spectrum_to_rgb_display,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SNAPSHOTS_DIR = "snapshots"
DISPLAY_TIMER_MS = 16   # ~60 fps display refresh (overridden in low-memory mode)
SYNC_TIMER_MS = 250     # camera value sync interval (overridden in low-memory mode)

VIEW_MODES = [
    "Raw",
    "Calibrated",
    "Reflectance Map",
    "Change Detection",
]


# ---------------------------------------------------------------------------
# Feature helpers
# ---------------------------------------------------------------------------

def safe_get(cam, feature_name, default=None):
    try:
        return getattr(cam, feature_name).get()
    except (AttributeError, vmbpy.VmbFeatureError):
        return default


def safe_set(cam, feature_name, value):
    try:
        getattr(cam, feature_name).set(value)
        return True
    except (AttributeError, vmbpy.VmbFeatureError):
        return False


def safe_get_range(cam, feature_name, default=(0, 100)):
    try:
        return getattr(cam, feature_name).get_range()
    except (AttributeError, vmbpy.VmbFeatureError):
        return default


# ---------------------------------------------------------------------------
# Scale helpers
# ---------------------------------------------------------------------------

def log_pos_to_value(pos, steps, val_min, val_max):
    if val_min <= 0:
        val_min = 1
    log_min = math.log10(val_min)
    log_max = math.log10(val_max)
    return 10 ** (log_min + (log_max - log_min) * pos / steps)


def value_to_log_pos(value, steps, val_min, val_max):
    if val_min <= 0:
        val_min = 1
    if value <= 0:
        value = val_min
    log_min = math.log10(val_min)
    log_max = math.log10(val_max)
    pos = int((math.log10(value) - log_min) / (log_max - log_min) * steps)
    return max(0, min(steps, pos))


# ---------------------------------------------------------------------------
# Frame conversion
# ---------------------------------------------------------------------------

def frame_to_numpy(frame):
    """Convert a VmbPy Frame to a numpy uint8 array (always a copy)."""
    pf = frame.get_pixel_format()
    if pf == vmbpy.PixelFormat.Mono8:
        return frame.as_opencv_image().copy()
    convertible = pf.get_convertible_formats()
    if vmbpy.PixelFormat.Mono8 in convertible:
        return frame.convert_pixel_format(
            vmbpy.PixelFormat.Mono8).as_opencv_image().copy()
    return frame.as_opencv_image().copy()


def numpy_to_qpixmap(image):
    """Convert a numpy image (mono uint8 or BGR uint8) to QPixmap."""
    if image.ndim == 3 and image.shape[2] == 1:
        image = image[:, :, 0]
    image = np.ascontiguousarray(image)
    h, w = image.shape[:2]

    if image.ndim == 2:
        qimg = QtGui.QImage(image.data, w, h, w,
                            QtGui.QImage.Format_Grayscale8).copy()
    else:
        bpl = w * image.shape[2]
        qimg = QtGui.QImage(image.data, w, h, bpl,
                            QtGui.QImage.Format_BGR888).copy()

    if qimg.isNull():
        return None
    return QtGui.QPixmap.fromImage(qimg)


# ---------------------------------------------------------------------------
# FPS counter
# ---------------------------------------------------------------------------

class FPSCounter:
    def __init__(self, window=60):
        self._window = window
        self._times = []
        self._lock = threading.Lock()

    def tick(self):
        with self._lock:
            self._times.append(time.time())
            if len(self._times) > self._window:
                self._times = self._times[-self._window:]

    def fps(self):
        with self._lock:
            if len(self._times) < 2:
                return 0.0
            dt = self._times[-1] - self._times[0]
            return (len(self._times) - 1) / dt if dt > 0 else 0.0


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def save_snapshot(image, prefix="snapshot"):
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(SNAPSHOTS_DIR, "{}_{}.png".format(prefix, ts))
    cv2.imwrite(path, image)
    return path


# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------

def list_cameras():
    with vmbpy.VmbSystem.get_instance() as vmb:
        cams = vmb.get_all_cameras()
        if not cams:
            print("No cameras found.")
            return
        print("Found {} camera(s):\n".format(len(cams)))
        for cam in cams:
            print("  Name         : {}".format(cam.get_name()))
            print("  Model        : {}".format(cam.get_model()))
            print("  Camera ID    : {}".format(cam.get_id()))
            print("  Serial Number: {}".format(cam.get_serial()))
            print("  Interface ID : {}".format(cam.get_interface_id()))
            print()


def get_camera(camera_id=None):
    with vmbpy.VmbSystem.get_instance() as vmb:
        if camera_id:
            try:
                return vmb.get_camera_by_id(camera_id)
            except vmbpy.VmbCameraError:
                print("ERROR: Cannot access camera '{}'.".format(camera_id))
                sys.exit(1)
        cams = vmb.get_all_cameras()
        if not cams:
            print("ERROR: No cameras found.")
            sys.exit(1)
        return cams[0]


def setup_camera(cam):
    try:
        cam.set_pixel_format(vmbpy.PixelFormat.Mono8)
    except (AttributeError, vmbpy.VmbFeatureError):
        pass
    safe_set(cam, "ExposureAuto", "Off")
    safe_set(cam, "GainAuto", "Off")


# ---------------------------------------------------------------------------
# Camera thread
# ---------------------------------------------------------------------------

class CameraThread(QtCore.QThread):
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, cam, parent=None):
        super().__init__(parent)
        self.cam = cam
        self._running = False
        self._latest_frame = None
        self._lock = threading.Lock()
        self.fps = FPSCounter()

    def run(self):
        self._running = True
        try:
            for frame in self.cam.get_frame_generator(timeout_ms=3000):
                if not self._running:
                    break
                if frame.get_status() == vmbpy.FrameStatus.Complete:
                    image = frame_to_numpy(frame)
                    with self._lock:
                        self._latest_frame = image
                    self.fps.tick()
        except vmbpy.VmbTimeout:
            pass
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def get_latest_frame(self):
        with self._lock:
            frame = self._latest_frame
            self._latest_frame = None
            return frame

    def stop(self):
        self._running = False
        self.wait(5000)


# ---------------------------------------------------------------------------
# FeatureSlider
# ---------------------------------------------------------------------------

class FeatureSlider(QtWidgets.QWidget):
    value_changed = QtCore.pyqtSignal(float)
    SLIDER_STEPS = 1000

    def __init__(self, min_val, max_val, decimals=0, log_scale=False,
                 suffix="", parent=None):
        super().__init__(parent)
        self.min_val = min_val
        self.max_val = max_val
        self.log_scale = log_scale
        self._updating = False

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, self.SLIDER_STEPS)
        self.slider.setTracking(True)

        self.spinbox = QtWidgets.QDoubleSpinBox()
        self.spinbox.setRange(min_val, max_val)
        self.spinbox.setDecimals(decimals)
        self.spinbox.setKeyboardTracking(False)
        if suffix:
            self.spinbox.setSuffix(suffix)
        span = max_val - min_val
        if decimals == 0:
            self.spinbox.setSingleStep(max(1, span / 100))
        else:
            self.spinbox.setSingleStep(round(span / 200, decimals))

        layout.addWidget(self.slider, stretch=3)
        layout.addWidget(self.spinbox, stretch=1)
        self.slider.valueChanged.connect(self._on_slider)
        self.spinbox.valueChanged.connect(self._on_spinbox)

    def _on_slider(self, pos):
        if self._updating:
            return
        self._updating = True
        val = self._pos_to_value(pos)
        self.spinbox.setValue(val)
        self.value_changed.emit(val)
        self._updating = False

    def _on_spinbox(self, val):
        if self._updating:
            return
        self._updating = True
        self.slider.setValue(self._value_to_pos(val))
        self.value_changed.emit(val)
        self._updating = False

    def set_value(self, val):
        self._updating = True
        self.spinbox.setValue(val)
        self.slider.setValue(self._value_to_pos(val))
        self._updating = False

    def value(self):
        return self.spinbox.value()

    def _pos_to_value(self, pos):
        if self.log_scale:
            return log_pos_to_value(pos, self.SLIDER_STEPS,
                                    self.min_val, self.max_val)
        return self.min_val + (self.max_val - self.min_val) * pos / self.SLIDER_STEPS

    def _value_to_pos(self, val):
        if self.log_scale:
            return value_to_log_pos(val, self.SLIDER_STEPS,
                                    self.min_val, self.max_val)
        if self.max_val == self.min_val:
            return 0
        pos = int((val - self.min_val) / (self.max_val - self.min_val)
                  * self.SLIDER_STEPS)
        return max(0, min(self.SLIDER_STEPS, pos))


# ===================================================================
#  TAB 1 -- Camera Controls
# ===================================================================

class CameraTab(QtWidgets.QWidget):
    snapshot_requested = QtCore.pyqtSignal()
    restart_requested = QtCore.pyqtSignal(dict)

    def __init__(self, cam, parent=None):
        super().__init__(parent)
        self.cam = cam
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Exposure
        grp = QtWidgets.QGroupBox("Exposure")
        fl = QtWidgets.QFormLayout(grp)
        self.exp_auto = QtWidgets.QComboBox()
        self.exp_auto.addItems(["Off", "Once", "Continuous"])
        self.exp_auto.setCurrentText(str(safe_get(self.cam, "ExposureAuto", "Off")))
        self.exp_auto.currentTextChanged.connect(self._on_exp_auto)
        fl.addRow("Auto:", self.exp_auto)
        exp_r = safe_get_range(self.cam, "ExposureTime", (26, 10000000))
        self.exp_slider = FeatureSlider(exp_r[0], exp_r[1], decimals=0,
                                        log_scale=True, suffix=" us")
        self.exp_slider.set_value(safe_get(self.cam, "ExposureTime", exp_r[0]))
        self.exp_slider.value_changed.connect(self._on_exposure)
        fl.addRow("Time:", self.exp_slider)
        layout.addWidget(grp)

        # Gain
        grp = QtWidgets.QGroupBox("Gain")
        fl = QtWidgets.QFormLayout(grp)
        self.gain_auto = QtWidgets.QComboBox()
        self.gain_auto.addItems(["Off", "Once", "Continuous"])
        self.gain_auto.setCurrentText(str(safe_get(self.cam, "GainAuto", "Off")))
        self.gain_auto.currentTextChanged.connect(self._on_gain_auto)
        fl.addRow("Auto:", self.gain_auto)
        gain_r = safe_get_range(self.cam, "Gain", (0, 48))
        self.gain_slider = FeatureSlider(gain_r[0], gain_r[1], decimals=1,
                                         suffix=" dB")
        self.gain_slider.set_value(safe_get(self.cam, "Gain", 0))
        self.gain_slider.value_changed.connect(self._on_gain)
        fl.addRow("Value:", self.gain_slider)
        layout.addWidget(grp)

        # Image
        grp = QtWidgets.QGroupBox("Image")
        fl = QtWidgets.QFormLayout(grp)
        bl_r = safe_get_range(self.cam, "BlackLevel", (0, 255))
        self.bl_slider = FeatureSlider(bl_r[0], bl_r[1], decimals=0)
        self.bl_slider.set_value(safe_get(self.cam, "BlackLevel", 0))
        self.bl_slider.value_changed.connect(
            lambda v: safe_set(self.cam, "BlackLevel", v))
        fl.addRow("Black Level:", self.bl_slider)
        gamma_r = safe_get_range(self.cam, "Gamma", (0.50, 2.50))
        self.gamma_slider = FeatureSlider(gamma_r[0], gamma_r[1], decimals=2)
        self.gamma_slider.set_value(safe_get(self.cam, "Gamma", 1.0))
        self.gamma_slider.value_changed.connect(
            lambda v: safe_set(self.cam, "Gamma", v))
        fl.addRow("Gamma:", self.gamma_slider)
        layout.addWidget(grp)

        # Format
        grp = QtWidgets.QGroupBox("Format")
        fl = QtWidgets.QFormLayout(grp)
        self.pixel_fmt = QtWidgets.QComboBox()
        self.pixel_fmt.addItems(["Mono8", "Mono10", "Mono12"])
        pf = str(safe_get(self.cam, "PixelFormat", "Mono8"))
        if pf in ["Mono8", "Mono10", "Mono12"]:
            self.pixel_fmt.setCurrentText(pf)
        self.pixel_fmt.currentTextChanged.connect(
            lambda f: self.restart_requested.emit({"PixelFormat": f}))
        fl.addRow("Pixel Format:", self.pixel_fmt)
        self.binning = QtWidgets.QComboBox()
        self.binning.addItems(["1x1", "2x2"])
        cb = safe_get(self.cam, "BinningHorizontal", 1)
        self.binning.setCurrentText("{}x{}".format(cb, cb))
        self.binning.currentTextChanged.connect(self._on_binning)
        fl.addRow("Binning:", self.binning)
        layout.addWidget(grp)

        # Actions
        grp = QtWidgets.QGroupBox("Actions")
        vl = QtWidgets.QVBoxLayout(grp)
        btn = QtWidgets.QPushButton("Save Snapshot  (Ctrl+S)")
        btn.clicked.connect(self.snapshot_requested.emit)
        vl.addWidget(btn)
        btn2 = QtWidgets.QPushButton("Reset ROI to Full Sensor")
        btn2.clicked.connect(self._on_roi_reset)
        vl.addWidget(btn2)
        layout.addWidget(grp)

        layout.addStretch()

    # -- slots --------------------------------------------------------------

    def _on_exposure(self, v):
        if str(safe_get(self.cam, "ExposureAuto", "Off")) == "Off":
            safe_set(self.cam, "ExposureTime", v)

    def _on_exp_auto(self, mode):
        safe_set(self.cam, "ExposureAuto", mode)
        self.exp_slider.setEnabled(mode == "Off")

    def _on_gain(self, v):
        if str(safe_get(self.cam, "GainAuto", "Off")) == "Off":
            safe_set(self.cam, "Gain", v)

    def _on_gain_auto(self, mode):
        safe_set(self.cam, "GainAuto", mode)
        self.gain_slider.setEnabled(mode == "Off")

    def _on_binning(self, text):
        b = int(text[0])
        self.restart_requested.emit({"BinningHorizontal": b,
                                     "BinningVertical": b})

    def _on_roi_reset(self):
        self.restart_requested.emit({
            "OffsetX": 0, "OffsetY": 0,
            "Width": safe_get(self.cam, "WidthMax", 2592),
            "Height": safe_get(self.cam, "HeightMax", 1944),
        })

    def sync_from_camera(self):
        ea = str(safe_get(self.cam, "ExposureAuto", "Off"))
        if ea != "Off":
            v = safe_get(self.cam, "ExposureTime", 0)
            if v:
                self.exp_slider.set_value(v)
        ga = str(safe_get(self.cam, "GainAuto", "Off"))
        if ga != "Off":
            v = safe_get(self.cam, "Gain", 0)
            if v is not None:
                self.gain_slider.set_value(v)


# ===================================================================
#  TAB 2 -- Calibration
# ===================================================================

class CalibrationTab(QtWidgets.QWidget):
    """Dark frame, flat field capture, and calibration toggle."""

    CAPTURE_FRAMES = 16  # number of frames to average

    def __init__(self, calibrator, parent=None):
        super().__init__(parent)
        self.calibrator = calibrator
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Dark frame
        grp = QtWidgets.QGroupBox("Dark Frame")
        vl = QtWidgets.QVBoxLayout(grp)
        lbl = QtWidgets.QLabel(
            "Cap the lens, then capture. Averages {} frames "
            "to reduce noise.".format(self.CAPTURE_FRAMES))
        lbl.setWordWrap(True)
        vl.addWidget(lbl)
        self.dark_btn = QtWidgets.QPushButton("Capture Dark Frame")
        self.dark_btn.clicked.connect(self._capture_dark_requested)
        vl.addWidget(self.dark_btn)
        self.dark_status = QtWidgets.QLabel("Status: not captured")
        vl.addWidget(self.dark_status)
        h = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("Save...")
        btn_save.clicked.connect(self._save_dark)
        h.addWidget(btn_save)
        btn_load = QtWidgets.QPushButton("Load...")
        btn_load.clicked.connect(self._load_dark)
        h.addWidget(btn_load)
        vl.addLayout(h)
        layout.addWidget(grp)

        # Flat field
        grp = QtWidgets.QGroupBox("Flat Field")
        vl = QtWidgets.QVBoxLayout(grp)
        lbl = QtWidgets.QLabel(
            "Point at a uniformly lit white surface, then capture. "
            "Corrects vignetting and pixel non-uniformity.")
        lbl.setWordWrap(True)
        vl.addWidget(lbl)
        self.flat_btn = QtWidgets.QPushButton("Capture Flat Field")
        self.flat_btn.clicked.connect(self._capture_flat_requested)
        vl.addWidget(self.flat_btn)
        self.flat_status = QtWidgets.QLabel("Status: not captured")
        vl.addWidget(self.flat_status)
        h = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("Save...")
        btn_save.clicked.connect(self._save_flat)
        h.addWidget(btn_save)
        btn_load = QtWidgets.QPushButton("Load...")
        btn_load.clicked.connect(self._load_flat)
        h.addWidget(btn_load)
        vl.addLayout(h)
        layout.addWidget(grp)

        # Calibration coefficient
        grp = QtWidgets.QGroupBox("Calibration Coefficient")
        fl = QtWidgets.QFormLayout(grp)
        self.coeff_spin = QtWidgets.QDoubleSpinBox()
        self.coeff_spin.setRange(0.0001, 100000.0)
        self.coeff_spin.setDecimals(4)
        self.coeff_spin.setValue(self.calibrator.calibration_coeff)
        self.coeff_spin.valueChanged.connect(self._on_coeff)
        fl.addRow("K:", self.coeff_spin)
        layout.addWidget(grp)

        # Save / load full calibration
        grp = QtWidgets.QGroupBox("Full Calibration File")
        hl = QtWidgets.QHBoxLayout(grp)
        btn = QtWidgets.QPushButton("Save All...")
        btn.clicked.connect(self._save_all)
        hl.addWidget(btn)
        btn = QtWidgets.QPushButton("Load All...")
        btn.clicked.connect(self._load_all)
        hl.addWidget(btn)
        btn = QtWidgets.QPushButton("Reset")
        btn.clicked.connect(self._reset)
        hl.addWidget(btn)
        layout.addWidget(grp)

        layout.addStretch()

        # Capture state
        self._capture_target = None  # "dark" or "flat"
        self._capture_remaining = 0

    # -- called from MainWindow each frame during a capture burst -----------

    def feed_frame(self, frame):
        """Accept a frame during a dark/flat capture burst."""
        if self._capture_remaining <= 0:
            return False

        if self._capture_target == "dark":
            self.calibrator.accumulate_dark_frame(frame)
        elif self._capture_target == "flat":
            self.calibrator.accumulate_flat_field(frame)

        self._capture_remaining -= 1

        if self._capture_remaining <= 0:
            if self._capture_target == "dark":
                self.calibrator.finalize_dark_frame()
                self.dark_status.setText(
                    "Status: captured ({} frames averaged)".format(
                        self.calibrator.dark_count))
            elif self._capture_target == "flat":
                self.calibrator.finalize_flat_field()
                self.flat_status.setText(
                    "Status: captured ({} frames averaged)".format(
                        self.calibrator.flat_count))
            self._capture_target = None
            self.dark_btn.setEnabled(True)
            self.flat_btn.setEnabled(True)
            return False  # done

        return True  # still capturing

    @property
    def is_capturing(self):
        return self._capture_remaining > 0

    # -- capture triggers ---------------------------------------------------

    def _capture_dark_requested(self):
        self.calibrator.dark_frame = None
        self.calibrator._dark_count = 0
        self._capture_target = "dark"
        self._capture_remaining = self.CAPTURE_FRAMES
        self.dark_btn.setEnabled(False)
        self.flat_btn.setEnabled(False)
        self.dark_status.setText("Capturing... ({} frames)".format(
            self.CAPTURE_FRAMES))

    def _capture_flat_requested(self):
        if hasattr(self.calibrator, '_flat_accumulator'):
            del self.calibrator._flat_accumulator
        self.calibrator.flat_field = None
        self.calibrator._flat_count = 0
        self._capture_target = "flat"
        self._capture_remaining = self.CAPTURE_FRAMES
        self.dark_btn.setEnabled(False)
        self.flat_btn.setEnabled(False)
        self.flat_status.setText("Capturing... ({} frames)".format(
            self.CAPTURE_FRAMES))

    # -- save / load individual ---------------------------------------------

    def _save_dark(self):
        if not self.calibrator.has_dark:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Dark Frame", "dark_frame.npy", "NumPy (*.npy)")
        if path:
            np.save(path, self.calibrator.dark_frame)

    def _load_dark(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Dark Frame", "", "NumPy (*.npy)")
        if path:
            self.calibrator.dark_frame = np.load(path).astype(np.float32)
            self.calibrator._dark_count = 1
            self.dark_status.setText("Status: loaded from file")

    def _save_flat(self):
        if not self.calibrator.has_flat:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Flat Field", "flat_field.npy", "NumPy (*.npy)")
        if path:
            np.save(path, self.calibrator.flat_field)

    def _load_flat(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Flat Field", "", "NumPy (*.npy)")
        if path:
            self.calibrator.flat_field = np.load(path).astype(np.float32)
            self.calibrator._flat_count = 1
            self.flat_status.setText("Status: loaded from file")

    def _on_coeff(self, val):
        self.calibrator.calibration_coeff = val

    # -- save / load all ----------------------------------------------------

    def _save_all(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Calibration", "calibration.npz", "NumPy (*.npz)")
        if path:
            self.calibrator.save(path)

    def _load_all(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Calibration", "", "NumPy (*.npz)")
        if path:
            self.calibrator.load(path)
            self.coeff_spin.setValue(self.calibrator.calibration_coeff)
            self.dark_status.setText(
                "Status: loaded" if self.calibrator.has_dark else "Status: none")
            self.flat_status.setText(
                "Status: loaded" if self.calibrator.has_flat else "Status: none")

    def _reset(self):
        self.calibrator.reset()
        self.dark_status.setText("Status: not captured")
        self.flat_status.setText("Status: not captured")
        self.coeff_spin.setValue(1.0)


# ===================================================================
#  TAB 3 -- Live Analysis
# ===================================================================

class AnalysisTab(QtWidgets.QWidget):
    """Real-time histogram, statistics, and change detection controls."""

    def __init__(self, change_detector, parent=None):
        super().__init__(parent)
        self.change_detector = change_detector
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Histogram
        grp = QtWidgets.QGroupBox("Intensity Histogram")
        vl = QtWidgets.QVBoxLayout(grp)
        self.hist_label = QtWidgets.QLabel()
        self.hist_label.setFixedHeight(180)
        self.hist_label.setStyleSheet("background-color: #000;")
        vl.addWidget(self.hist_label)
        layout.addWidget(grp)

        # Statistics
        grp = QtWidgets.QGroupBox("Image Statistics")
        fl = QtWidgets.QFormLayout(grp)
        self.stat_min = QtWidgets.QLabel("--")
        self.stat_max = QtWidgets.QLabel("--")
        self.stat_mean = QtWidgets.QLabel("--")
        self.stat_std = QtWidgets.QLabel("--")
        self.stat_median = QtWidgets.QLabel("--")
        fl.addRow("Min:", self.stat_min)
        fl.addRow("Max:", self.stat_max)
        fl.addRow("Mean:", self.stat_mean)
        fl.addRow("Std Dev:", self.stat_std)
        fl.addRow("Median:", self.stat_median)
        layout.addWidget(grp)

        # Change detection
        grp = QtWidgets.QGroupBox("Change Detection")
        vl = QtWidgets.QVBoxLayout(grp)
        lbl = QtWidgets.QLabel(
            "Set a reference frame, then switch view to "
            "'Change Detection' to see what changed.")
        lbl.setWordWrap(True)
        vl.addWidget(lbl)
        self.ref_btn = QtWidgets.QPushButton("Set Current Frame as Reference")
        self.ref_btn.clicked.connect(self._set_reference)
        vl.addWidget(self.ref_btn)
        self.ref_status = QtWidgets.QLabel("Reference: not set")
        vl.addWidget(self.ref_status)
        btn_clear = QtWidgets.QPushButton("Clear Reference")
        btn_clear.clicked.connect(self._clear_reference)
        vl.addWidget(btn_clear)
        layout.addWidget(grp)

        layout.addStretch()

        self._pending_ref = False

    def update_histogram(self, image):
        """Render and display the histogram for *image*."""
        w = self.hist_label.width() or 320
        hist_img = render_histogram(image, width=w, height=180)
        pm = numpy_to_qpixmap(hist_img)
        if pm:
            self.hist_label.setPixmap(pm)

    def update_statistics(self, image):
        """Update the statistics labels."""
        s = compute_statistics(image)
        self.stat_min.setText("{:.1f}".format(s["min"]))
        self.stat_max.setText("{:.1f}".format(s["max"]))
        self.stat_mean.setText("{:.2f}".format(s["mean"]))
        self.stat_std.setText("{:.2f}".format(s["std"]))
        self.stat_median.setText("{:.1f}".format(s["median"]))

    def _set_reference(self):
        self._pending_ref = True
        self.ref_status.setText("Reference: waiting for next frame...")

    def feed_reference(self, frame):
        """Called from MainWindow to set the reference from a live frame."""
        if self._pending_ref:
            self.change_detector.set_reference(frame)
            self._pending_ref = False
            self.ref_status.setText("Reference: set")
            return True
        return False

    def _clear_reference(self):
        self.change_detector.reset()
        self.ref_status.setText("Reference: not set")


# ===================================================================
#  TAB 4 -- Vegetation Indices
# ===================================================================

class VegetationTab(QtWidgets.QWidget):
    """Band capture, index computation, and result display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.red_band = None
        self.nir_band = None
        self.broadband_band = None
        self.exposure_red_us = None
        self.exposure_nir_us = None
        self.index_result = None
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Digital simulation mode
        grp_digital = QtWidgets.QGroupBox("Band source")
        vl_d = QtWidgets.QVBoxLayout(grp_digital)
        self.digital_mode_combo = QtWidgets.QComboBox()
        self.digital_mode_combo.addItem("Physical filters (Red + NIR)")
        self.digital_mode_combo.addItem("Digital: single frame (synthetic)")
        self.digital_mode_combo.addItem("Digital: dual exposure")
        self.digital_mode_combo.currentIndexChanged.connect(self._on_digital_mode_changed)
        fl_d = QtWidgets.QFormLayout()
        fl_d.addRow("Mode:", self.digital_mode_combo)
        vl_d.addLayout(fl_d)
        self.broadband_row = QtWidgets.QHBoxLayout()
        self.broadband_btn = QtWidgets.QPushButton("Capture broadband")
        self.broadband_btn.clicked.connect(lambda: self._request_capture("broadband"))
        self.broadband_btn.setVisible(False)
        self.broadband_row.addWidget(self.broadband_btn)
        self.broadband_status = QtWidgets.QLabel("Broadband: not captured")
        self.broadband_status.setVisible(False)
        self.broadband_row.addWidget(self.broadband_status)
        vl_d.addLayout(self.broadband_row)
        hint = QtWidgets.QLabel(
            "Single frame: one capture, Red/NIR simulated from brightness. "
            "Dual exposure: capture long then short; exposures used for normalization.")
        hint.setWordWrap(True)
        vl_d.addWidget(hint)
        layout.addWidget(grp_digital)

        # Band capture
        grp = QtWidgets.QGroupBox("Band Images")
        vl = QtWidgets.QVBoxLayout(grp)
        lbl = QtWidgets.QLabel(
            "Capture or load single-band images taken through "
            "bandpass filters (e.g. 660 nm Red, 850 nm NIR), or use digital simulation above.")
        lbl.setWordWrap(True)
        vl.addWidget(lbl)

        hl = QtWidgets.QHBoxLayout()
        self.red_btn = QtWidgets.QPushButton("Capture as Red Band (or long exp)")
        self.red_btn.clicked.connect(lambda: self._request_capture("red"))
        hl.addWidget(self.red_btn)
        btn = QtWidgets.QPushButton("Load...")
        btn.clicked.connect(lambda: self._load_band("red"))
        hl.addWidget(btn)
        vl.addLayout(hl)
        self.red_status = QtWidgets.QLabel("Red band: not loaded")
        vl.addWidget(self.red_status)

        hl = QtWidgets.QHBoxLayout()
        self.nir_btn = QtWidgets.QPushButton("Capture as NIR Band (or short exp)")
        self.nir_btn.clicked.connect(lambda: self._request_capture("nir"))
        hl.addWidget(self.nir_btn)
        btn = QtWidgets.QPushButton("Load...")
        btn.clicked.connect(lambda: self._load_band("nir"))
        hl.addWidget(btn)
        vl.addLayout(hl)
        self.nir_status = QtWidgets.QLabel("NIR band: not loaded")
        vl.addWidget(self.nir_status)
        layout.addWidget(grp)

        # Index selector
        grp = QtWidgets.QGroupBox("Compute Vegetation Index")
        vl = QtWidgets.QVBoxLayout(grp)
        fl = QtWidgets.QFormLayout()
        self.index_combo = QtWidgets.QComboBox()
        self.index_combo.addItems(list(VEGETATION_INDICES.keys()))
        fl.addRow("Index:", self.index_combo)
        self.align_check = QtWidgets.QCheckBox("Align bands (ORB registration)")
        self.align_check.setChecked(True)
        fl.addRow(self.align_check)
        vl.addLayout(fl)
        self.compute_btn = QtWidgets.QPushButton("Compute")
        self.compute_btn.clicked.connect(self._compute_index)
        vl.addWidget(self.compute_btn)
        layout.addWidget(grp)

        # Spectral radiance
        grp_spec = QtWidgets.QGroupBox("Spectral radiance")
        vl_spec = QtWidgets.QVBoxLayout(grp_spec)
        self.spectral_mode_combo = QtWidgets.QComboBox()
        self.spectral_mode_combo.addItem("Two-point (660, 850 nm)")
        self.spectral_mode_combo.addItem("Unmixing (vegetation + soil)")
        fl_spec = QtWidgets.QFormLayout()
        fl_spec.addRow("Mode:", self.spectral_mode_combo)
        vl_spec.addLayout(fl_spec)
        self.spectral_btn = QtWidgets.QPushButton("Compute spectral radiance")
        self.spectral_btn.clicked.connect(self._compute_spectral_radiance)
        vl_spec.addWidget(self.spectral_btn)
        layout.addWidget(grp_spec)

        # Result display
        grp = QtWidgets.QGroupBox("Result")
        vl = QtWidgets.QVBoxLayout(grp)
        self.result_label = QtWidgets.QLabel()
        self.result_label.setFixedHeight(200)
        self.result_label.setAlignment(QtCore.Qt.AlignCenter)
        self.result_label.setStyleSheet("background-color: #111;")
        vl.addWidget(self.result_label)
        self.result_stats = QtWidgets.QLabel("")
        self.result_stats.setWordWrap(True)
        vl.addWidget(self.result_stats)
        hl = QtWidgets.QHBoxLayout()
        btn = QtWidgets.QPushButton("Save Result Image...")
        btn.clicked.connect(self._save_result)
        hl.addWidget(btn)
        vl.addLayout(hl)
        layout.addWidget(grp)

        layout.addStretch()

        self._pending_capture = None  # "red", "nir", or "broadband"

    def _on_digital_mode_changed(self, index):
        if index == 1:
            self.broadband_btn.setVisible(True)
            self.broadband_status.setVisible(True)
        else:
            self.broadband_btn.setVisible(False)
            self.broadband_status.setVisible(False)
        if index == 2:
            self.red_btn.setText("Capture long exp (Red-like)")
            self.nir_btn.setText("Capture short exp (NIR-like)")
        else:
            self.red_btn.setText("Capture as Red Band")
            self.nir_btn.setText("Capture as NIR Band")

    # -- capture bands from live feed ---------------------------------------

    def _request_capture(self, band):
        self._pending_capture = band

    def feed_frame(self, frame, exposure_us=None):
        """Accept a live frame for band capture. exposure_us used for dual-exposure mode."""
        if self._pending_capture is None:
            return False
        band = self._pending_capture
        self._pending_capture = None

        img = frame.copy()
        if img.ndim == 3 and img.shape[2] == 1:
            img = img[:, :, 0]

        if band == "red":
            self.red_band = img
            self.exposure_red_us = exposure_us if exposure_us is not None else 1000.0
            h, w = img.shape[:2]
            self.red_status.setText(
                "Red band: captured ({}x{}), {:.0f} us".format(w, h, self.exposure_red_us))
        elif band == "nir":
            self.nir_band = img
            self.exposure_nir_us = exposure_us if exposure_us is not None else 1000.0
            h, w = img.shape[:2]
            self.nir_status.setText(
                "NIR band: captured ({}x{}), {:.0f} us".format(w, h, self.exposure_nir_us))
        elif band == "broadband":
            self.broadband_band = img
            h, w = img.shape[:2]
            self.broadband_status.setText("Broadband: captured ({}x{})".format(w, h))
        return True

    # -- load bands from file -----------------------------------------------

    def _load_band(self, band):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load {} Band".format(band.upper()), "",
            "Images (*.png *.tif *.tiff *.npy);;All (*)")
        if not path:
            return
        if path.endswith(".npy"):
            img = np.load(path)
        else:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return
        if img.ndim == 3 and img.shape[2] == 1:
            img = img[:, :, 0]

        h, w = img.shape[:2]
        if band == "red":
            self.red_band = img
            self.red_status.setText("Red band: loaded ({}x{})".format(w, h))
        elif band == "nir":
            self.nir_band = img
            self.nir_status.setText("NIR band: loaded ({}x{})".format(w, h))

    # -- compute index -------------------------------------------------------

    def _compute_index(self):
        mode = self.digital_mode_combo.currentIndex()
        red = None
        nir = None

        if mode == 1:
            # Digital: single frame (synthetic)
            if self.broadband_band is None:
                self.result_stats.setText("Capture a broadband frame first.")
                return
            red, nir = simulate_red_nir_synthetic(self.broadband_band)
        elif mode == 2:
            # Digital: dual exposure
            if self.red_band is None or self.nir_band is None:
                self.result_stats.setText(
                    "Capture both long-exposure (Red-like) and short-exposure (NIR-like) frames.")
                return
            exp_red = self.exposure_red_us if self.exposure_red_us is not None else 1000.0
            exp_nir = self.exposure_nir_us if self.exposure_nir_us is not None else 1000.0
            red, nir = simulate_red_nir_dual_exposure(
                self.nir_band, self.red_band,
                exp_nir, exp_red,
                short_as_nir=True,
            )
        else:
            # Physical filters
            if self.red_band is None or self.nir_band is None:
                self.result_stats.setText(
                    "Both Red and NIR bands are required.")
                return
            nir = self.nir_band.astype(np.float32)
            red = self.red_band.astype(np.float32)
            if self.align_check.isChecked():
                nir_u8 = self.nir_band if self.nir_band.dtype == np.uint8 \
                    else (nir / (nir.max() or 1) * 255).astype(np.uint8)
                red_u8 = self.red_band if self.red_band.dtype == np.uint8 \
                    else (red / (red.max() or 1) * 255).astype(np.uint8)
                aligned, H = align_bands(red_u8, nir_u8)
                if H is not None:
                    nir = aligned.astype(np.float32)

        name = self.index_combo.currentText()
        if name == "NDVI":
            idx = compute_ndvi(nir, red)
        elif name == "GNDVI":
            from processing import compute_gndvi
            idx = compute_gndvi(nir, red)
        elif name == "SAVI":
            idx = compute_savi(nir, red)
        elif name == "EVI":
            idx = compute_evi(nir, red)
        else:
            idx = compute_ndvi(nir, red)

        self.index_result = idx

        # Display coloured result
        info = VEGETATION_INDICES.get(name, {})
        vmin, vmax = info.get("range", (-1, 1))
        colored = ndvi_colormap(idx) if name in ("NDVI", "GNDVI") \
            else index_colormap(idx, vmin=vmin, vmax=vmax)

        pm = numpy_to_qpixmap(colored)
        if pm:
            scaled = pm.scaled(self.result_label.size(),
                               QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
            self.result_label.setPixmap(scaled)

        # Statistics
        stats = compute_statistics(idx)
        self.result_stats.setText(
            "{}: min={:.3f}  max={:.3f}  mean={:.3f}  std={:.3f}".format(
                name, stats["min"], stats["max"],
                stats["mean"], stats["std"]))

    def _get_red_nir_float(self):
        """Return (red, nir) float32 arrays for spectral radiance; (None, None) if unavailable."""
        mode = self.digital_mode_combo.currentIndex()
        if mode == 1:
            if self.broadband_band is None:
                return None, None
            return simulate_red_nir_synthetic(self.broadband_band)
        if mode == 2:
            if self.red_band is None or self.nir_band is None:
                return None, None
            exp_red = self.exposure_red_us if self.exposure_red_us is not None else 1000.0
            exp_nir = self.exposure_nir_us if self.exposure_nir_us is not None else 1000.0
            return simulate_red_nir_dual_exposure(
                self.nir_band, self.red_band, exp_nir, exp_red, short_as_nir=True)
        if self.red_band is None or self.nir_band is None:
            return None, None
        red = self.red_band.astype(np.float32)
        nir = self.nir_band.astype(np.float32)
        if self.align_check.isChecked():
            nir_u8 = self.nir_band if self.nir_band.dtype == np.uint8 \
                else (nir / (nir.max() or 1) * 255).astype(np.uint8)
            red_u8 = self.red_band if self.red_band.dtype == np.uint8 \
                else (red / (red.max() or 1) * 255).astype(np.uint8)
            aligned, H = align_bands(red_u8, nir_u8)
            if H is not None:
                nir = aligned.astype(np.float32)
        return red, nir

    def _compute_spectral_radiance(self):
        red, nir = self._get_red_nir_float()
        if red is None or nir is None:
            self.result_stats.setText(
                "Red and NIR bands required. Capture or load both, or use digital mode.")
            return
        use_unmixing = self.spectral_mode_combo.currentIndex() == 1
        if use_unmixing:
            wl, spectrum, _ = spectral_radiance_from_unmixing(red, nir)
            rgb = spectrum_to_rgb_display(spectrum, wl, r_nm=650, g_nm=550, b_nm=450)
        else:
            wl, spectrum = two_point_spectral_radiance(
                red, nir, interpolate_to_grid=True)
            rgb = spectrum_to_rgb_display(spectrum, wl, r_nm=660, g_nm=660, b_nm=850)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        pm = numpy_to_qpixmap(bgr)
        if pm:
            scaled = pm.scaled(self.result_label.size(),
                               QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
            self.result_label.setPixmap(scaled)
        mode_name = "Unmixing (veg+soil)" if use_unmixing else "Two-point (660, 850 nm)"
        self.result_stats.setText(
            "Spectral radiance: {} | {} wavelengths".format(mode_name, len(wl)))

    def _save_result(self):
        if self.index_result is None:
            return
        name = self.index_combo.currentText()
        info = VEGETATION_INDICES.get(name, {})
        vmin, vmax = info.get("range", (-1, 1))
        colored = ndvi_colormap(self.index_result) \
            if name in ("NDVI", "GNDVI") \
            else index_colormap(self.index_result, vmin=vmin, vmax=vmax)
        path = save_snapshot(colored, prefix=name.lower())
        self.result_stats.setText(
            self.result_stats.text() + "\nSaved: {}".format(path))


# ===================================================================
#  Main window
# ===================================================================

class MainWindow(QtWidgets.QMainWindow):

    def __init__(self, cam):
        super().__init__()
        self.cam = cam
        self._last_full_image = None

        # Processing objects
        self.calibrator = RadiometricCalibrator()
        self.change_detector = ChangeDetector()

        self.setWindowTitle("Alvium 1800 U-501m NIR")
        self.resize(1300, 850)

        # -- Menu bar -------------------------------------------------------
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        sa = file_menu.addAction("Save Snapshot")
        sa.setShortcut("Ctrl+S")
        sa.triggered.connect(self._on_snapshot)
        file_menu.addSeparator()
        qa = file_menu.addAction("Quit")
        qa.setShortcut("Ctrl+Q")
        qa.triggered.connect(self.close)

        # -- Central layout -------------------------------------------------
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # Left side: view mode selector + live view
        left = QtWidgets.QVBoxLayout()

        # View mode toolbar
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(QtWidgets.QLabel("View:"))
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(VIEW_MODES)
        self.view_mode.setCurrentText("Raw")
        toolbar.addWidget(self.view_mode)

        self.cal_check = QtWidgets.QCheckBox("Apply Calibration")
        toolbar.addWidget(self.cal_check)
        toolbar.addStretch()
        left.addLayout(toolbar)

        # Live view
        self.view_label = QtWidgets.QLabel("Waiting for frames...")
        self.view_label.setAlignment(QtCore.Qt.AlignCenter)
        self.view_label.setMinimumSize(640, 480)
        self.view_label.setStyleSheet("background-color: #1e1e1e;")
        left.addWidget(self.view_label, stretch=1)

        main_layout.addLayout(left, stretch=3)

        # Right side: tabbed control panels
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setMinimumWidth(320)
        self.tabs.setMaximumWidth(400)

        self.camera_tab = CameraTab(cam)
        self.calibration_tab = CalibrationTab(self.calibrator)
        self.analysis_tab = AnalysisTab(self.change_detector)
        self.vegetation_tab = VegetationTab()

        self._wrap_tab(self.tabs, "Camera", self.camera_tab)
        self._wrap_tab(self.tabs, "Calibration", self.calibration_tab)
        self._wrap_tab(self.tabs, "Analysis", self.analysis_tab)
        self._wrap_tab(self.tabs, "Vegetation", self.vegetation_tab)

        main_layout.addWidget(self.tabs, stretch=0)

        # Connect signals
        self.camera_tab.snapshot_requested.connect(self._on_snapshot)
        self.camera_tab.restart_requested.connect(self._restart_stream)

        # -- Status bar -----------------------------------------------------
        self.fps_label = QtWidgets.QLabel("FPS: --")
        self.res_label = QtWidgets.QLabel("")
        self.statusBar().addWidget(self.fps_label)
        self.statusBar().addPermanentWidget(self.res_label)

        # -- Camera thread --------------------------------------------------
        self.cam_thread = CameraThread(cam, parent=self)
        self.cam_thread.error_occurred.connect(self._on_cam_error)
        self.cam_thread.start()

        # -- Timers ---------------------------------------------------------
        self.display_timer = QtCore.QTimer(self)
        self.display_timer.timeout.connect(self._update_display)
        self.display_timer.start(DISPLAY_TIMER_MS)

        self.sync_timer = QtCore.QTimer(self)
        self.sync_timer.timeout.connect(self._sync)
        self.sync_timer.start(SYNC_TIMER_MS)

    @staticmethod
    def _wrap_tab(tabs, title, widget):
        """Wrap a widget in a QScrollArea and add it as a tab."""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        tabs.addTab(scroll, title)

    # -- display pipeline ---------------------------------------------------

    def _update_display(self):
        raw = self.cam_thread.get_latest_frame()
        if raw is None:
            return

        # Normalize shape
        if raw.ndim == 3 and raw.shape[2] == 1:
            raw = raw[:, :, 0]

        self._last_full_image = raw

        # Feed frames to subsystems that need them
        self.calibration_tab.feed_frame(raw)
        self.analysis_tab.feed_reference(raw)
        exposure_us = safe_get(self.cam, "ExposureTime", 1000.0) or 1000.0
        self.vegetation_tab.feed_frame(raw, exposure_us=exposure_us)

        # Get current exposure and gain for calibration
        gain_db = safe_get(self.cam, "Gain", 0.0) or 0.0

        # Determine what to display
        mode = self.view_mode.currentText()
        apply_cal = self.cal_check.isChecked()

        if mode == "Raw" and not apply_cal:
            display = raw

        elif mode == "Raw" and apply_cal:
            display = self.calibrator.calibrate_display(
                raw, exposure_us, gain_db)

        elif mode == "Calibrated":
            display = self.calibrator.calibrate_display(
                raw, exposure_us, gain_db)

        elif mode == "Reflectance Map":
            if apply_cal and (self.calibrator.has_dark
                              or self.calibrator.has_flat):
                cal = self.calibrator.calibrate(raw, exposure_us, gain_db)
                display = reflectance_colormap(cal)
            else:
                display = reflectance_colormap(raw)

        elif mode == "Change Detection":
            if self.change_detector.has_reference:
                display = self.change_detector.compute_display(raw)
            else:
                display = raw

        else:
            display = raw

        # Convert to QPixmap and show
        pm = numpy_to_qpixmap(display)
        if pm:
            scaled = pm.scaled(self.view_label.size(),
                               QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
            self.view_label.setPixmap(scaled)

        # Update analysis tab
        self.analysis_tab.update_histogram(raw)
        self.analysis_tab.update_statistics(raw)

        # Status bar
        h, w = raw.shape[:2]
        fps = self.cam_thread.fps.fps()
        self.fps_label.setText("  FPS: {:.1f}  ".format(fps))
        pf = safe_get(self.cam, "PixelFormat", "")
        cal_tag = " [CAL]" if apply_cal else ""
        self.res_label.setText("{}x{}  {}{}  ".format(w, h, pf, cal_tag))

    # -- periodic sync ------------------------------------------------------

    def _sync(self):
        self.camera_tab.sync_from_camera()

    # -- snapshot -----------------------------------------------------------

    def _on_snapshot(self):
        if self._last_full_image is not None:
            path = save_snapshot(self._last_full_image)
            self.statusBar().showMessage(
                "Snapshot saved: {}".format(path), 3000)

    # -- stream restart -----------------------------------------------------

    def _restart_stream(self, settings):
        self.statusBar().showMessage("Restarting stream...")
        self.cam_thread.stop()
        for name, value in settings.items():
            safe_set(self.cam, name, value)
        self.cam_thread = CameraThread(self.cam, parent=self)
        self.cam_thread.error_occurred.connect(self._on_cam_error)
        self.cam_thread.start()
        self.statusBar().showMessage("Stream restarted.", 2000)

    # -- error handling -----------------------------------------------------

    def _on_cam_error(self, msg):
        self.statusBar().showMessage("Camera error: {}".format(msg), 5000)

    # -- cleanup ------------------------------------------------------------

    def closeEvent(self, event):
        self.display_timer.stop()
        self.sync_timer.stop()
        self.cam_thread.stop()
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def launch_gui(camera_id=None):
    with vmbpy.VmbSystem.get_instance():
        cam = get_camera(camera_id)
        try:
            cam.__enter__()
        except vmbpy.VmbCameraError as exc:
            print("ERROR: Could not open camera: {}".format(exc))
            print("  Make sure no other application (e.g. Vimba Viewer) "
                  "is using it.")
            sys.exit(1)
        try:
            setup_camera(cam)
            app = QtWidgets.QApplication(sys.argv)
            app.setStyle("Fusion")
            window = MainWindow(cam)
            window.show()
            exit_code = app.exec_()
        finally:
            cam.__exit__(None, None, None)
        sys.exit(exit_code)


def parse_args():
    p = argparse.ArgumentParser(
        description="Allied Vision Alvium USB Camera - Control Application")
    p.add_argument("--list", action="store_true",
                   help="List connected cameras and exit")
    p.add_argument("--camera-id", type=str, default=None,
                   help="Camera ID (default: first detected)")
    p.add_argument("--low-memory", action="store_true",
                   help="Slower UI refresh for low-RAM systems (e.g. Raspberry Pi 4 2GB)")
    return p.parse_args()


def main():
    global DISPLAY_TIMER_MS, SYNC_TIMER_MS
    args = parse_args()
    if args.low_memory or os.environ.get("SWC_LOW_MEMORY", "").strip().lower() in ("1", "true", "yes"):
        DISPLAY_TIMER_MS = 50   # ~20 fps to reduce CPU/memory on Pi
        SYNC_TIMER_MS = 500
    if args.list:
        list_cameras()
        return
    launch_gui(args.camera_id)


if __name__ == "__main__":
    main()
