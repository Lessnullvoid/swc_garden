"""SC Module Explorer -- Real-time tuning GUI with learned presets.

Launch:  python -m exploration.explorer_gui
"""
from __future__ import annotations

import json
import logging
import sys
import time as _time
from pathlib import Path

import numpy as np
import soundfile as sf

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from exploration import ml_engine, preset_library
from exploration.module_defs import (MODULES, get_module_names,
                                    get_multifx_algo_info,
                                    get_granular_algo_info, get_param_defs)
from exploration.sc_bridge import SCBridge

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


class ParamSlider(QWidget):
    """Single parameter row: label + slider + spinbox."""

    def __init__(self, pdef: dict, on_change) -> None:
        super().__init__()
        self.pdef = pdef
        self._on_change = on_change
        self._updating = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.label = QLabel(pdef["label"])
        self.label.setFixedWidth(180)
        layout.addWidget(self.label)

        self.slider = QSlider(Qt.Horizontal)
        self._scale = 1000
        self.slider.setMinimum(int(pdef["min"] * self._scale))
        self.slider.setMaximum(int(pdef["max"] * self._scale))
        self.slider.setValue(int(pdef["default"] * self._scale))
        self.slider.valueChanged.connect(self._slider_moved)
        layout.addWidget(self.slider, stretch=1)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(pdef["min"], pdef["max"])
        self.spin.setSingleStep(pdef["step"])
        self.spin.setDecimals(self._decimals())
        self.spin.setValue(pdef["default"])
        self.spin.setFixedWidth(90)
        self.spin.valueChanged.connect(self._spin_changed)
        layout.addWidget(self.spin)

    def _decimals(self) -> int:
        s = self.pdef["step"]
        if s >= 1:
            return 1
        if s >= 0.01:
            return 3
        return 4

    def _slider_moved(self, raw: int) -> None:
        if self._updating:
            return
        val = raw / self._scale
        self._updating = True
        self.spin.setValue(val)
        self._updating = False
        self._on_change(self.pdef, val)

    def _spin_changed(self, val: float) -> None:
        if self._updating:
            return
        self._updating = True
        self.slider.setValue(int(val * self._scale))
        self._updating = False
        self._on_change(self.pdef, val)

    def set_value(self, val: float) -> None:
        self._updating = True
        self.slider.setValue(int(val * self._scale))
        self.spin.setValue(val)
        self._updating = False

    def value(self) -> float:
        return self.spin.value()


class WaveformWidget(QWidget):
    """Draws a peak-envelope waveform with a moving playhead."""

    BG = QColor(30, 30, 30)
    WAVE_COLOR = QColor(80, 180, 80)
    HEAD_COLOR = QColor(220, 60, 60)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(80)
        self._peaks: np.ndarray | None = None
        self._position = 0.0

    def set_audio(self, samples: np.ndarray) -> None:
        """Precompute a peak envelope from raw audio samples."""
        if samples.ndim > 1:
            samples = samples[:, 0]
        n = len(samples)
        if n == 0:
            self._peaks = None
            self.update()
            return
        num_bins = max(1, min(n, 2000))
        bin_size = n // num_bins
        trimmed = samples[: bin_size * num_bins].reshape(num_bins, bin_size)
        self._peaks = np.max(np.abs(trimmed), axis=1)
        peak_max = self._peaks.max()
        if peak_max > 0:
            self._peaks /= peak_max
        self.update()

    def set_position(self, fraction: float) -> None:
        self._position = max(0.0, min(1.0, fraction))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.BG)
        w = self.width()
        h = self.height()
        mid = h / 2

        if self._peaks is not None and len(self._peaks) > 0:
            pen = QPen(self.WAVE_COLOR)
            pen.setWidth(1)
            p.setPen(pen)

            num = len(self._peaks)
            for i in range(num):
                x = int(i * w / num)
                amp = self._peaks[i] * mid * 0.9
                p.drawLine(x, int(mid - amp), x, int(mid + amp))

        if self._position > 0:
            pen = QPen(self.HEAD_COLOR)
            pen.setWidth(2)
            p.setPen(pen)
            hx = int(self._position * w)
            p.drawLine(hx, 0, hx, h)

        p.end()


class ExplorerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SC Module Explorer")
        self.setMinimumSize(960, 700)

        self.bridge = SCBridge()
        self._playing = False
        self._current_module: str | None = None
        self._data_features: dict[str, float] = {}
        self._dataset: dict | None = None
        self._sliders: dict[str, ParamSlider] = {}
        self._loaded_audio: str | None = None
        self._audio_duration = 0.0
        self._play_start_time = 0.0

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_data_bar())

        root.addWidget(self._build_module_bar())

        root.addWidget(self._build_waveform_panel())

        body = QSplitter(Qt.Horizontal)
        body.addWidget(self._build_params_panel())
        body.addWidget(self._build_sidebar())
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 1)
        root.addWidget(body, stretch=1)

        root.addWidget(self._build_transport())

        self._playback_timer = QTimer()
        self._playback_timer.setInterval(50)
        self._playback_timer.timeout.connect(self._tick_playback)

        self._select_module(get_module_names()[0])

    # -- UI builders ----------------------------------------------------------

    def _build_data_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 4, 4, 4)

        self.data_label = QLabel("No dataset loaded")
        layout.addWidget(self.data_label, stretch=1)

        self.feat_bright = QLabel("Bright: --")
        self.feat_ndvi = QLabel("NDVI: --")
        self.feat_cs = QLabel("Change: --")
        for w in (self.feat_bright, self.feat_ndvi, self.feat_cs):
            w.setFixedWidth(110)
            layout.addWidget(w)

        btn = QPushButton("Load Dataset...")
        btn.clicked.connect(self._load_dataset_dialog)
        layout.addWidget(btn)

        return bar

    def _build_module_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.addWidget(QLabel("Module:"))

        self.module_combo = QComboBox()
        for name in get_module_names():
            self.module_combo.addItem(MODULES[name]["label"], name)
        self.module_combo.currentIndexChanged.connect(
            lambda _: self._select_module(self.module_combo.currentData())
        )
        layout.addWidget(self.module_combo, stretch=1)
        return bar

    def _build_waveform_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        self.waveform = WaveformWidget()
        layout.addWidget(self.waveform)

        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        self.time_label = QLabel("0:00.0 / 0:00.0")
        self.time_label.setFixedWidth(140)
        self.time_label.setStyleSheet("font-family: monospace; font-size: 12px;")
        time_row.addWidget(self.time_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setRange(0, 10000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        time_row.addWidget(self.progress_bar, stretch=1)
        layout.addLayout(time_row)

        return panel

    def _build_params_panel(self) -> QWidget:
        self.params_container = QWidget()
        self.params_layout = QVBoxLayout(self.params_container)
        self.params_layout.setContentsMargins(4, 4, 4, 4)
        return self.params_container

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(4, 4, 4, 4)

        grp_proposals = QGroupBox("Proposals")
        pl = QVBoxLayout(grp_proposals)
        self.proposal_list = QListWidget()
        pl.addWidget(self.proposal_list)
        btn_load_proposal = QPushButton("Load Selected")
        btn_load_proposal.clicked.connect(self._load_proposal)
        pl.addWidget(btn_load_proposal)
        layout.addWidget(grp_proposals)

        grp_presets = QGroupBox("Preset Library")
        prl = QVBoxLayout(grp_presets)
        self.preset_list = QListWidget()
        prl.addWidget(self.preset_list)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.preset_name_edit = QLineEdit()
        name_row.addWidget(self.preset_name_edit)
        prl.addLayout(name_row)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save Current")
        btn_save.clicked.connect(self._save_preset)
        btn_row.addWidget(btn_save)
        btn_load = QPushButton("Load")
        btn_load.clicked.connect(self._load_preset)
        btn_row.addWidget(btn_load)
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._delete_preset)
        btn_row.addWidget(btn_del)
        prl.addLayout(btn_row)
        layout.addWidget(grp_presets)

        return sidebar

    def _build_transport(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 4, 4, 4)

        self.btn_boot = QPushButton("Boot SC")
        self.btn_boot.clicked.connect(self._boot_sc)
        layout.addWidget(self.btn_boot)

        self.btn_load_audio = QPushButton("Load Audio...")
        self.btn_load_audio.clicked.connect(self._load_audio_dialog)
        layout.addWidget(self.btn_load_audio)

        self.audio_label = QLabel("No audio loaded")
        self.audio_label.setStyleSheet("color: #888;")
        layout.addWidget(self.audio_label)

        self.btn_play = QPushButton("Play")
        self.btn_play.clicked.connect(self._play)
        layout.addWidget(self.btn_play)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self._stop)
        layout.addWidget(self.btn_stop)

        layout.addStretch()

        self.activity_label = QLabel("Activity: --")
        layout.addWidget(self.activity_label)

        self.status_label = QLabel("Status: idle")
        layout.addWidget(self.status_label)

        return bar

    # -- Module switching -----------------------------------------------------

    def _select_module(self, module: str) -> None:
        self._current_module = module
        self._rebuild_sliders(module)
        self._refresh_presets()
        self._refresh_proposals()

        if self._playing:
            self._stop()

    def _rebuild_sliders(self, module: str) -> None:
        while self.params_layout.count():
            item = self.params_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._sliders.clear()
        self._algo_label = None

        if module in ("advanced_effects", "granular_sampling"):
            self._algo_label = QLabel("")
            self._algo_label.setStyleSheet(
                "font-weight: bold; color: #6ca; padding: 2px 4px;"
                "background: #222; border-radius: 3px;"
            )
            self.params_layout.addWidget(self._algo_label)

        for pdef in get_param_defs(module):
            slider = ParamSlider(pdef, self._on_param_change)
            self._sliders[pdef["name"]] = slider
            self.params_layout.addWidget(slider)

        self.params_layout.addStretch()

        if module in ("advanced_effects", "granular_sampling"):
            self._update_macro_labels(0.0)

    # -- Parameter change callback -------------------------------------------

    def _on_param_change(self, pdef: dict, value: float) -> None:
        if self._playing and self.bridge.is_running:
            self.bridge.set_param(pdef["sc_arg"], value)

        if (self._current_module in ("advanced_effects", "granular_sampling")
                and pdef["name"] == "algo"):
            self._update_macro_labels(value)

    def _update_macro_labels(self, algo_value: float) -> None:
        """Update the algo label to reflect the current algorithm."""
        if self._current_module == "advanced_effects":
            info = get_multifx_algo_info(algo_value)
            if self._algo_label is not None:
                self._algo_label.setText(f"Algorithm: {info['name']}")
            macro_map = {"param1": info["p1"], "param2": info["p2"],
                         "param3": info["p3"]}
            for pname, description in macro_map.items():
                slider = self._sliders.get(pname)
                if slider:
                    slider.label.setText(f"P{pname[-1]}: {description}")

        elif self._current_module == "granular_sampling":
            info = get_granular_algo_info(algo_value)
            if self._algo_label is not None:
                self._algo_label.setText(
                    f"Algorithm: {info['name']}  --  {info['desc']}"
                )

    # -- Transport controls ---------------------------------------------------

    def _boot_sc(self) -> None:
        self.status_label.setText("Status: booting SC...")
        QApplication.processEvents()
        self.bridge.boot()
        if self._loaded_audio:
            self.bridge.load_buffer(self._loaded_audio)
        else:
            self._try_auto_load_buffer()
        self.status_label.setText("Status: SC ready")

    def _load_audio_dialog(self) -> None:
        start_dir = str(BASE_DIR / "audio_batches")
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Audio File", start_dir,
            "Audio (*.wav *.aif *.aiff)"
        )
        if not path:
            return
        self._set_audio_file(path)

        if self.bridge.is_running:
            self.bridge.load_buffer(path)
            self.status_label.setText("Status: buffer loaded")

    def _set_audio_file(self, path: str) -> None:
        self._loaded_audio = path
        name = Path(path).name
        self.audio_label.setText(name)
        self.audio_label.setStyleSheet("color: #2a2;")
        log.info("Selected audio: %s", path)

        try:
            samples, sr = sf.read(path, dtype="float32")
            self._audio_duration = len(samples) / sr
            self.waveform.set_audio(samples)
            self._update_time_display(0.0)
        except Exception as e:
            log.error("Failed to read audio for waveform: %s", e)
            self._audio_duration = 0.0

    def _try_auto_load_buffer(self) -> None:
        if not self._current_module:
            return
        audio_dir = BASE_DIR / "audio_batches" / self._current_module
        if not audio_dir.exists():
            return
        files = (
            list(audio_dir.glob("*.wav"))
            + list(audio_dir.glob("*.aif"))
            + list(audio_dir.glob("*.aiff"))
        )
        if files:
            self._set_audio_file(str(files[0]))
            self.bridge.load_buffer(self._loaded_audio)
            log.info("Auto-loaded buffer: %s", files[0].name)

    def _play(self) -> None:
        if not self.bridge.is_running:
            self.status_label.setText("Status: boot SC first")
            return
        if not self._loaded_audio:
            self.status_label.setText("Status: load audio first")
            return
        if self._playing:
            self._stop()

        self.bridge.load_buffer(self._loaded_audio)
        self.bridge.start_module(self._current_module)

        for pdef in get_param_defs(self._current_module):
            slider = self._sliders.get(pdef["name"])
            if slider:
                self.bridge.set_param(pdef["sc_arg"], slider.value())

        self._playing = True
        self._play_start_time = _time.monotonic()
        self._playback_timer.start()
        self.status_label.setText("Status: playing " + self._current_module)

    def _stop(self) -> None:
        self._playback_timer.stop()
        self.bridge.stop()
        self._playing = False
        self.waveform.set_position(0.0)
        self._update_time_display(0.0)
        self.status_label.setText("Status: stopped")

    def _tick_playback(self) -> None:
        if not self._playing or self._audio_duration <= 0:
            return

        elapsed = _time.monotonic() - self._play_start_time
        rate = self._get_effective_rate()
        position_sec = (elapsed * rate) % self._audio_duration
        fraction = position_sec / self._audio_duration

        self.waveform.set_position(fraction)
        self.progress_bar.setValue(int(fraction * 10000))
        self._update_time_display(position_sec)

    def _get_effective_rate(self) -> float:
        if self._current_module == "granular_sampling":
            slider = self._sliders.get("pos_rate")
            if slider:
                return slider.value() * 44100.0
        slider = self._sliders.get("rate")
        if slider:
            return slider.value()
        return 1.0

    def _update_time_display(self, pos_sec: float) -> None:
        dur = self._audio_duration
        self.time_label.setText(
            f"{self._fmt_time(pos_sec)} / {self._fmt_time(dur)}"
        )

    @staticmethod
    def _fmt_time(sec: float) -> str:
        m = int(sec) // 60
        s = sec - m * 60
        return f"{m}:{s:04.1f}"

    # -- Dataset loading ------------------------------------------------------

    def _load_dataset_dialog(self) -> None:
        datasets_dir = str(BASE_DIR / "daily_datasets")
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Dataset JSON", datasets_dir, "JSON (*.json)"
        )
        if path:
            self._load_dataset(Path(path))

    def _load_dataset(self, path: Path) -> None:
        try:
            self._dataset = json.loads(path.read_text())
        except Exception as e:
            log.error("Failed to load dataset: %s", e)
            return

        date = self._dataset.get("date", path.parent.name)
        total = self._dataset.get("global", {}).get("total_events", "?")
        self.data_label.setText(f"Dataset: {date} ({total} events)")

        bright = self._extract_max("brightness_mean", "mean")
        ndvi = self._extract_max("ndvi_mean", "mean")
        cs = self._extract_max("change_score", "mean")

        self._data_features = {
            "brightness_mean": bright,
            "ndvi_mean": ndvi,
            "change_score_mean": cs,
        }

        self.feat_bright.setText(f"Bright: {bright:.3f}")
        self.feat_ndvi.setText(f"NDVI: {ndvi:.3f}")
        self.feat_cs.setText(f"Change: {cs:.3f}")

        self._update_activity()
        self._refresh_proposals()

    def _extract_max(self, key: str, sub: str) -> float:
        vals = []
        for cam in ("cam01", "cam02"):
            try:
                vals.append(float(self._dataset[cam][key][sub]))
            except (KeyError, TypeError):
                pass
        return max(vals) if vals else 0.0

    def _update_activity(self) -> None:
        b = self._data_features.get("brightness_mean", 0)
        n = self._data_features.get("ndvi_mean", 0)
        c = self._data_features.get("change_score_mean", 0)
        activity = 0.35 * c + 0.35 * b + 0.3 * n
        self.activity_label.setText(f"Activity: {activity:.3f}")

    # -- Presets --------------------------------------------------------------

    def _refresh_presets(self) -> None:
        self.preset_list.clear()
        if not self._current_module:
            return
        for p in preset_library.load_presets(self._current_module):
            item = QListWidgetItem(p["name"])
            item.setData(Qt.UserRole, p)
            self.preset_list.addItem(item)

    def _save_preset(self) -> None:
        name = self.preset_name_edit.text().strip()
        if not name or not self._current_module:
            return

        params = {}
        for pname, slider in self._sliders.items():
            params[pname] = round(slider.value(), 6)

        preset_library.save_preset(
            self._current_module, name, params, self._data_features
        )
        self._refresh_presets()
        self._refresh_proposals()

    def _load_preset(self) -> None:
        item = self.preset_list.currentItem()
        if not item:
            return
        preset = item.data(Qt.UserRole)
        self._apply_params(preset.get("params", {}))

    def _delete_preset(self) -> None:
        item = self.preset_list.currentItem()
        if not item:
            return
        preset_library.delete_preset(self._current_module, item.text())
        self._refresh_presets()
        self._refresh_proposals()

    def _apply_params(self, params: dict[str, float]) -> None:
        for pname, val in params.items():
            slider = self._sliders.get(pname)
            if slider:
                slider.set_value(val)
                if self._playing and self.bridge.is_running:
                    self.bridge.set_param(slider.pdef["sc_arg"], val)

        if (self._current_module == "advanced_effects"
                and "algo" in params):
            self._update_macro_labels(params["algo"])

    # -- ML Proposals ---------------------------------------------------------

    def _refresh_proposals(self) -> None:
        self.proposal_list.clear()
        if not self._current_module or not self._data_features:
            return

        results = ml_engine.propose(
            self._current_module, self._data_features, top_n=5
        )
        for preset, dist in results:
            text = f"{preset['name']}  (dist: {dist:.4f})"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, preset)
            self.proposal_list.addItem(item)

    def _load_proposal(self) -> None:
        item = self.proposal_list.currentItem()
        if not item:
            return
        preset = item.data(Qt.UserRole)
        self._apply_params(preset.get("params", {}))

    # -- Cleanup --------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._stop()
        self.bridge.shutdown()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ExplorerWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
