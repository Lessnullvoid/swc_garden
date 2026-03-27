"""
Mac-side monitoring GUI for incoming Pi camera images.

Watches incoming_data/ for new frames from the Pis, applies dark/flat
calibration (Alvium cameras) or reads depth data (Arducam ToF), extracts
features, writes JSON events, and displays everything in a live PyQt5
interface.

Supports 3 cameras:
  - cam01, cam02: Allied Vision Alvium (monochrome NIR) -- reflectance map
  - cam03: Arducam ToF depth camera -- depth colormap + RGB

Usage:
    python monitor_app.py

No VmbPy or camera connection required -- works purely with image files.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.dates as mdates

from processing import RadiometricCalibrator, reflectance_colormap

PROJECT_ROOT = Path(__file__).resolve().parent
INCOMING_DIR = PROJECT_ROOT / "incoming_data"
CALIBRATION_DIR = PROJECT_ROOT / "calibration"
CAMERA_IDS = ("cam01", "cam02", "cam03")

TOF_CAMERAS = {"cam03"}

CAM_COLORS = {
    "cam01": "#4fc3f7",
    "cam02": "#81c784",
    "cam03": "#ffb74d",
}


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
# Feature helpers for Alvium (NIR mono) cameras
# ---------------------------------------------------------------------------

def compute_brightness(img):
    return float(np.mean(img) / 255.0)


def compute_ndvi_digital(img):
    median = float(np.median(img))
    nir_mask = img >= median
    red_mask = ~nir_mask
    nir_mean = float(np.mean(img[nir_mask])) if np.any(nir_mask) else 128.0
    red_mean = float(np.mean(img[red_mask])) if np.any(red_mask) else 128.0
    denom = nir_mean + red_mean
    if denom < 1e-6:
        return 0.0
    return (nir_mean - red_mean) / denom


def compute_change_score(img, ref_path):
    img_u8 = np.clip(img, 0, 255).astype(np.uint8)
    if not ref_path.exists():
        np.save(str(ref_path), img_u8)
        return 0.0
    ref = np.load(str(ref_path))
    if ref.shape != img_u8.shape:
        np.save(str(ref_path), img_u8)
        return 0.0
    diff = cv2.absdiff(img_u8, ref)
    score = float(np.mean(diff) / 255.0)
    np.save(str(ref_path), img_u8)
    return score


# ---------------------------------------------------------------------------
# Feature helpers for ToF depth cameras
# ---------------------------------------------------------------------------

def depth_to_colormap(depth, depth_range=4.0):
    """Convert float32 depth array to a BGR colormap image."""
    if depth_range <= 0:
        depth_range = 4.0
    normalized = np.clip(depth / depth_range, 0.0, 1.0)
    gray = (normalized * 255).astype(np.uint8)
    return cv2.applyColorMap(gray, cv2.COLORMAP_JET)


def compute_depth_change(depth, ref_path):
    """Compute change score on depth data (normalized 0-1)."""
    if not ref_path.exists():
        np.save(str(ref_path), depth)
        return 0.0
    ref = np.load(str(ref_path))
    if ref.shape != depth.shape:
        np.save(str(ref_path), depth)
        return 0.0
    diff = np.abs(depth - ref)
    max_range = max(float(np.nanmax(depth)), 1.0)
    score = float(np.nanmean(diff) / max_range)
    np.save(str(ref_path), depth)
    return score


# ---------------------------------------------------------------------------
# Time-series chart with per-camera lines
# ---------------------------------------------------------------------------

class TimeSeriesChart(FigureCanvasQTAgg):
    """Matplotlib chart showing feature evolution over time, one line per camera.

    Subplot 1: Brightness (NIR) / Depth Mean (ToF)
    Subplot 2: NDVI (NIR) / Depth Std (ToF)
    Subplot 3: Change score (all cameras)
    """

    def __init__(self, parent=None):
        self.fig = Figure(figsize=(10, 2.5), dpi=100)
        self.fig.set_facecolor("#2b2b2b")
        super().__init__(self.fig)
        self.setMinimumHeight(190)

        self.ax_brightness = self.fig.add_subplot(131)
        self.ax_ndvi = self.fig.add_subplot(132)
        self.ax_change = self.fig.add_subplot(133)

        self._series = {}
        for cam_id in CAMERA_IDS:
            self._series[cam_id] = {
                "times": [],
                "brightness": [],
                "ndvi": [],
                "change": [],
            }

        self._init_axes()
        self.fig.subplots_adjust(
            left=0.06, right=0.98, top=0.82, bottom=0.22, wspace=0.3
        )

    def _init_axes(self):
        for ax, title in [
            (self.ax_brightness, "Brightness / Depth"),
            (self.ax_ndvi, "NDVI / Depth Std"),
            (self.ax_change, "Change"),
        ]:
            ax.set_facecolor("#1e1e1e")
            ax.set_title(title, color="#ccc", fontsize=10, pad=4)
            ax.tick_params(colors="#aaa", labelsize=7)
            for spine in ax.spines.values():
                spine.set_color("#555")
            ax.grid(True, alpha=0.2, color="#666")

    def add_point(self, cam_id, timestamp_str, brightness, ndvi, change):
        if cam_id not in self._series:
            self._series[cam_id] = {
                "times": [], "brightness": [], "ndvi": [], "change": [],
            }

        try:
            t = datetime.fromisoformat(timestamp_str)
        except Exception:
            t = datetime.now(timezone.utc)

        s = self._series[cam_id]
        s["times"].append(t)
        s["brightness"].append(brightness)
        s["ndvi"].append(ndvi)
        s["change"].append(change)

        self._redraw()

    def _redraw(self):
        plot_defs = [
            (self.ax_brightness, "brightness", "Brightness / Depth", None),
            (self.ax_ndvi, "ndvi", "NDVI / Depth Std", None),
            (self.ax_change, "change", "Change", None),
        ]

        for ax, key, title, _ in plot_defs:
            ax.clear()
            ax.set_facecolor("#1e1e1e")
            ax.grid(True, alpha=0.2, color="#666")
            ax.tick_params(colors="#aaa", labelsize=7)
            for spine in ax.spines.values():
                spine.set_color("#555")
            ax.set_title(title, color="#ccc", fontsize=10, pad=4)

            has_data = False
            for cam_id in CAMERA_IDS:
                s = self._series.get(cam_id)
                if not s or not s["times"]:
                    continue
                color = CAM_COLORS.get(cam_id, "#ffffff")
                is_tof = cam_id in TOF_CAMERAS
                label = cam_id
                if is_tof and key == "brightness":
                    label = f"{cam_id} (depth)"
                elif is_tof and key == "ndvi":
                    label = f"{cam_id} (std)"

                ax.plot(
                    s["times"], s[key],
                    color=color, linewidth=1.5,
                    marker="o", markersize=3, alpha=0.9,
                    label=label,
                )
                ax.fill_between(s["times"], s[key], alpha=0.08, color=color)
                has_data = True

            if has_data:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                ax.legend(
                    loc="upper left", fontsize=7,
                    facecolor="#2b2b2b", edgecolor="#555",
                    labelcolor="#ccc",
                )

                all_vals = []
                for cam_id in CAMERA_IDS:
                    s = self._series.get(cam_id)
                    if s and s[key]:
                        all_vals.extend(s[key])
                if all_vals:
                    ymax = max(max(all_vals) * 1.2, 0.01)
                    ax.set_ylim(0, ymax)

        self.fig.autofmt_xdate(rotation=30, ha="right")
        self.draw_idle()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MonitorWindow(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SWC Garden Monitor")
        self.resize(1300, 1000)

        self._calibrators = {}
        self._history = []
        self._processed_files = set()

        self._load_calibration()
        self._build_ui()
        self._setup_watcher()
        self._scan_existing()

    def _load_calibration(self):
        CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        for cam_id in CAMERA_IDS:
            if cam_id in TOF_CAMERAS:
                continue
            cal = RadiometricCalibrator()
            dark_path = CALIBRATION_DIR / f"{cam_id}_dark.npy"
            flat_path = CALIBRATION_DIR / f"{cam_id}_flat.npy"
            if dark_path.exists():
                cal.dark_frame = np.load(str(dark_path)).astype(np.float32)
                cal._dark_count = 1
            if flat_path.exists():
                cal.flat_field = np.load(str(flat_path)).astype(np.float32)
                cal._flat_count = 1
            self._calibrators[cam_id] = cal

    # -- UI ----------------------------------------------------------------

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        cam_row = QtWidgets.QHBoxLayout()
        self._cam_widgets = {}

        for cam_id in CAMERA_IDS:
            col = QtWidgets.QVBoxLayout()
            color = CAM_COLORS.get(cam_id, "#ccc")
            is_tof = cam_id in TOF_CAMERAS

            cam_label = f"{cam_id} (ToF)" if is_tof else cam_id
            header = QtWidgets.QLabel(cam_label)
            header.setAlignment(QtCore.Qt.AlignCenter)
            header.setStyleSheet(
                f"font-size: 14px; font-weight: bold; color: {color};"
            )
            col.addWidget(header)

            if is_tof:
                img_depth = QtWidgets.QLabel(f"Waiting for node {cam_id}...")
                img_depth.setAlignment(QtCore.Qt.AlignCenter)
                img_depth.setMinimumSize(300, 110)
                img_depth.setStyleSheet(
                    "background-color: #1a1a1a; color: #555; font-size: 12px;"
                )
                col.addWidget(img_depth, stretch=1)

                img_rgb = QtWidgets.QLabel("RGB: waiting...")
                img_rgb.setAlignment(QtCore.Qt.AlignCenter)
                img_rgb.setMinimumSize(300, 110)
                img_rgb.setStyleSheet(
                    "background-color: #1a1a1a; color: #555; font-size: 12px;"
                )
                col.addWidget(img_rgb, stretch=1)
            else:
                img_depth = QtWidgets.QLabel(f"Waiting for node {cam_id}...")
                img_depth.setAlignment(QtCore.Qt.AlignCenter)
                img_depth.setMinimumSize(300, 220)
                img_depth.setStyleSheet(
                    "background-color: #1a1a1a; color: #555; font-size: 13px;"
                )
                col.addWidget(img_depth, stretch=1)
                img_rgb = None

            feat_frame = QtWidgets.QFrame()
            feat_layout = QtWidgets.QHBoxLayout(feat_frame)
            feat_layout.setContentsMargins(4, 2, 4, 2)
            feat_layout.setSpacing(8)

            if is_tof:
                lbl_b = QtWidgets.QLabel("D: --")
                lbl_n = QtWidgets.QLabel("S: --")
                lbl_c = QtWidgets.QLabel("C: --")
            else:
                lbl_b = QtWidgets.QLabel("B: --")
                lbl_n = QtWidgets.QLabel("N: --")
                lbl_c = QtWidgets.QLabel("C: --")

            for lbl in (lbl_b, lbl_n, lbl_c):
                lbl.setStyleSheet("font-size: 11px;")
                feat_layout.addWidget(lbl)

            col.addWidget(feat_frame)
            cam_row.addLayout(col, stretch=1)

            self._cam_widgets[cam_id] = {
                "image": img_depth,
                "image_rgb": img_rgb,
                "feat1": lbl_b,
                "feat2": lbl_n,
                "change": lbl_c,
            }

        root.addLayout(cam_row, stretch=3)

        self.chart = TimeSeriesChart()
        root.addWidget(self.chart, stretch=2)

        table_row = QtWidgets.QHBoxLayout()
        self._cam_tables = {}

        for cam_id in CAMERA_IDS:
            col = QtWidgets.QVBoxLayout()
            color = CAM_COLORS.get(cam_id, "#ccc")
            is_tof = cam_id in TOF_CAMERAS

            lbl = QtWidgets.QLabel(f"{cam_id} history")
            lbl.setStyleSheet(
                f"font-size: 11px; font-weight: bold; color: {color};"
            )
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            col.addWidget(lbl)

            tbl = QtWidgets.QTableWidget()
            tbl.setColumnCount(5)
            if is_tof:
                tbl.setHorizontalHeaderLabels(
                    ["#", "Time", "Depth", "Std", "Change"]
                )
            else:
                tbl.setHorizontalHeaderLabels(
                    ["#", "Time", "B", "NDVI", "Change"]
                )
            tbl.horizontalHeader().setStretchLastSection(True)
            tbl.horizontalHeader().setSectionResizeMode(
                QtWidgets.QHeaderView.Stretch
            )
            tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            tbl.verticalHeader().setVisible(False)
            tbl.setAlternatingRowColors(True)
            tbl.setStyleSheet("font-size: 10px;")
            tbl.itemSelectionChanged.connect(
                lambda c=cam_id: self._on_table_select(c)
            )
            col.addWidget(tbl)

            table_row.addLayout(col, stretch=1)
            self._cam_tables[cam_id] = tbl

        root.addLayout(table_row, stretch=2)

        self.status_frames = QtWidgets.QLabel("Frames: 0")
        self.status_cal = QtWidgets.QLabel("Calibration: loading...")
        self.status_last = QtWidgets.QLabel("Last: --")
        self.statusBar().addWidget(self.status_frames)
        self.statusBar().addWidget(self.status_cal)
        self.statusBar().addPermanentWidget(self.status_last)

        self._update_cal_status()

    def _update_cal_status(self):
        parts = []
        for cam_id in CAMERA_IDS:
            if cam_id in TOF_CAMERAS:
                parts.append(f"{cam_id}: ToF (factory)")
                continue
            cal = self._calibrators.get(cam_id)
            if cal is None:
                continue
            flags = []
            if cal.has_dark:
                flags.append("dark")
            if cal.has_flat:
                flags.append("flat")
            if flags:
                parts.append(f"{cam_id}: {'+'.join(flags)}")
            else:
                parts.append(f"{cam_id}: none")
        self.status_cal.setText("Cal: " + "  |  ".join(parts))

    # -- File watching -----------------------------------------------------

    def _setup_watcher(self):
        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)

        INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        self._watcher.addPath(str(INCOMING_DIR))

        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        for d in [today.isoformat(), yesterday.isoformat()]:
            day_dir = INCOMING_DIR / d
            if day_dir.is_dir():
                self._watch_day_dir(day_dir)

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.timeout.connect(self._poll_for_new)
        self._poll_timer.start(5000)

    def _watch_day_dir(self, day_dir):
        if str(day_dir) not in self._watcher.directories():
            self._watcher.addPath(str(day_dir))
        for cam_dir in day_dir.iterdir():
            if cam_dir.is_dir() and str(cam_dir) not in self._watcher.directories():
                self._watcher.addPath(str(cam_dir))

    def _on_directory_changed(self, path):
        p = Path(path)
        if p.is_dir():
            for sub in p.iterdir():
                if sub.is_dir() and str(sub) not in self._watcher.directories():
                    self._watcher.addPath(str(sub))
        self._poll_for_new()

    def _scan_existing(self):
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        for d in [yesterday.isoformat(), today.isoformat()]:
            day_dir = INCOMING_DIR / d
            if not day_dir.is_dir():
                continue
            for cam_dir in sorted(day_dir.iterdir()):
                if not cam_dir.is_dir():
                    continue
                for meta in sorted(cam_dir.glob("*.meta.json")):
                    self._try_process_meta(meta)

    def _poll_for_new(self):
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        for d in [today.isoformat(), yesterday.isoformat()]:
            day_dir = INCOMING_DIR / d
            if day_dir.is_dir():
                self._watch_day_dir(day_dir)
                for cam_dir in sorted(day_dir.iterdir()):
                    if not cam_dir.is_dir():
                        continue
                    for meta in sorted(cam_dir.glob("*.meta.json")):
                        self._try_process_meta(meta)

    # -- Processing --------------------------------------------------------

    def _try_process_meta(self, meta_path):
        """Process a .meta.json file and its associated data files."""
        key = str(meta_path)
        if key in self._processed_files:
            return

        stem = meta_path.name.replace(".meta.json", "")
        cam_id = meta_path.parent.name

        with open(meta_path) as f:
            meta = json.load(f)

        is_tof = meta.get("camera_type") == "tof"

        if is_tof:
            self._process_tof(meta_path.parent, stem, cam_id, meta)
        else:
            self._process_alvium(meta_path.parent, stem, cam_id, meta)

        self._processed_files.add(key)

    def _process_alvium(self, parent, stem, cam_id, meta):
        """Process an Alvium NIR mono frame."""
        jpg_path = parent / f"{stem}.jpg"
        if not jpg_path.exists():
            return

        frame = cv2.imread(str(jpg_path), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            return

        cal = self._calibrators.get(cam_id)
        if cal and (cal.has_dark or cal.has_flat):
            exposure_us = meta.get("exposure_us", 1000.0)
            gain_db = meta.get("gain_db", 0.0)
            calibrated = cal.calibrate_display(frame, exposure_us, gain_db)
            cal_float = cal.calibrate(frame, exposure_us, gain_db)
        else:
            calibrated = frame.copy()
            cal_float = frame.astype(np.float32)

        ref_path = CALIBRATION_DIR / f"{cam_id}_ref.npy"
        features = {
            "brightness_mean": round(compute_brightness(calibrated), 4),
            "ndvi_mean": round(compute_ndvi_digital(calibrated), 4),
            "change_score": round(compute_change_score(calibrated, ref_path), 4),
        }

        ts = meta.get("timestamp", datetime.now(timezone.utc).isoformat())
        event_path = parent / f"{stem}.json"
        if not event_path.exists():
            event = {"timestamp": ts, "camera_id": cam_id, "features": features}
            event_path.write_text(json.dumps(event, indent=2))

        refl = reflectance_colormap(cal_float)

        entry = {
            "index": len(self._history) + 1,
            "timestamp": ts,
            "camera_id": cam_id,
            "camera_type": "alvium",
            "features": features,
            "display_frame": refl,
            "display_rgb": None,
        }
        self._history.append(entry)
        self._update_display(cam_id, entry)
        self._add_table_row(entry)

        self.chart.add_point(
            cam_id, ts,
            features["brightness_mean"],
            features["ndvi_mean"],
            features["change_score"],
        )
        self._update_status(ts, cam_id)

    def _process_tof(self, parent, stem, cam_id, meta):
        """Process an Arducam ToF depth frame."""
        depth_path = parent / f"{stem}.depth.npy"
        if not depth_path.exists():
            return

        depth = np.load(str(depth_path))
        depth_range = meta.get("depth_range", 4.0)

        ref_path = CALIBRATION_DIR / f"{cam_id}_ref.npy"
        depth_mean = round(float(np.nanmean(depth)), 4)
        depth_std = round(float(np.nanstd(depth)), 4)
        change = round(compute_depth_change(depth, ref_path), 4)

        features = {
            "brightness_mean": depth_mean,
            "ndvi_mean": depth_std,
            "change_score": change,
        }

        ts = meta.get("timestamp", datetime.now(timezone.utc).isoformat())
        event_path = parent / f"{stem}.json"
        if not event_path.exists():
            event = {
                "timestamp": ts,
                "camera_id": cam_id,
                "camera_type": "tof",
                "features": {
                    "depth_mean": depth_mean,
                    "depth_std": depth_std,
                    "change_score": change,
                },
            }
            event_path.write_text(json.dumps(event, indent=2))

        depth_color = depth_to_colormap(depth, depth_range)

        rgb_frame = None
        jpg_path = parent / f"{stem}.jpg"
        if jpg_path.exists():
            rgb_frame = cv2.imread(str(jpg_path))

        entry = {
            "index": len(self._history) + 1,
            "timestamp": ts,
            "camera_id": cam_id,
            "camera_type": "tof",
            "features": features,
            "display_frame": depth_color,
            "display_rgb": rgb_frame,
        }
        self._history.append(entry)
        self._update_display(cam_id, entry)
        self._add_table_row(entry)

        self.chart.add_point(
            cam_id, ts, depth_mean, depth_std, change,
        )
        self._update_status(ts, cam_id)

    # -- UI updates --------------------------------------------------------

    def _update_display(self, cam_id, entry):
        widgets = self._cam_widgets.get(cam_id)
        if widgets is None:
            return

        pm = numpy_to_qpixmap(entry["display_frame"])
        if pm:
            widgets["image"].setPixmap(pm.scaled(
                widgets["image"].size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            ))

        if widgets["image_rgb"] is not None and entry["display_rgb"] is not None:
            pm_rgb = numpy_to_qpixmap(entry["display_rgb"])
            if pm_rgb:
                widgets["image_rgb"].setPixmap(pm_rgb.scaled(
                    widgets["image_rgb"].size(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                ))

        f = entry["features"]
        is_tof = cam_id in TOF_CAMERAS
        if is_tof:
            widgets["feat1"].setText(f"D: {f['brightness_mean']:.3f}m")
            widgets["feat2"].setText(f"S: {f['ndvi_mean']:.3f}m")
        else:
            widgets["feat1"].setText(f"B: {f['brightness_mean']:.4f}")
            widgets["feat2"].setText(f"N: {f['ndvi_mean']:.4f}")
        widgets["change"].setText(f"C: {f['change_score']:.4f}")

    def _add_table_row(self, entry):
        cam_id = entry["camera_id"]
        tbl = self._cam_tables.get(cam_id)
        if tbl is None:
            return

        row = 0
        tbl.insertRow(row)

        f = entry["features"]
        try:
            t = datetime.fromisoformat(entry["timestamp"])
            time_str = t.strftime("%H:%M:%S")
        except Exception:
            time_str = entry["timestamp"]

        is_tof = cam_id in TOF_CAMERAS
        if is_tof:
            items = [
                str(entry["index"]),
                time_str,
                f"{f['brightness_mean']:.3f}",
                f"{f['ndvi_mean']:.3f}",
                f"{f['change_score']:.4f}",
            ]
        else:
            items = [
                str(entry["index"]),
                time_str,
                f"{f['brightness_mean']:.3f}",
                f"{f['ndvi_mean']:.3f}",
                f"{f['change_score']:.4f}",
            ]

        for col, text in enumerate(items):
            item = QtWidgets.QTableWidgetItem(text)
            item.setTextAlignment(QtCore.Qt.AlignCenter)
            tbl.setItem(row, col, item)

    def _update_status(self, ts, cam_id):
        self.status_frames.setText(f"Frames: {len(self._history)}")
        try:
            t = datetime.fromisoformat(ts)
            self.status_last.setText(
                f"Last: {t.strftime('%H:%M:%S UTC')}  ({cam_id})"
            )
        except Exception:
            self.status_last.setText(f"Last: {ts}")

    def _on_table_select(self, cam_id):
        """Show the frame selected in a per-camera history table."""
        tbl = self._cam_tables.get(cam_id)
        if tbl is None:
            return
        rows = tbl.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        idx_item = tbl.item(row, 0)
        if idx_item is None:
            return
        try:
            idx = int(idx_item.text()) - 1
        except ValueError:
            return
        if 0 <= idx < len(self._history):
            entry = self._history[idx]
            if entry["camera_id"] == cam_id:
                self._update_display(cam_id, entry)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MonitorWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
