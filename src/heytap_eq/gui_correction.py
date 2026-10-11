"""Inline correction generator: live measurement → target fits, applied only on request.

The preview never edits the session. Every result is tagged with the signature of its
inputs (measurement, target, settings, firmware estimate context); a result whose
signature no longer matches the window is discarded instead of being shown or applied.
"""

import copy
import json
import threading

import numpy as np
from PySide6 import QtCore, QtWidgets

from heytap_eq.estimation import measurement_key
from heytap_eq.fitting import FitOptions, fit_response
from heytap_eq.widgets import button, label, mark

SMOOTHING = (("不平滑", 0.), ("1/24 倍频程", 1/24), ("1/12 倍频程", 1/12), ("1/6 倍频程", 1/6),
             ("1/3 倍频程", 1/3))
UNSET = object()  # "no preview requested yet"; distinct from None, which means inputs are missing.
CONTEXT_KEYS = ("preset", "region", "state", "rate", "reference_preset", "reference_state", "crossover",
                "gain_mode")


class PreviewWorker(QtCore.QThread):
    # Carries the worker itself: mixin methods are not registered slots, so sender() is unavailable.
    completed = QtCore.Signal(object)

    def __init__(self, fn, signature, parent):
        super().__init__(parent)
        self.fn = fn
        self.signature = signature
        self.result = None

    def run(self):
        try:
            self.result = (self.fn(), None)
        except Exception as exc:
            self.result = (None, exc)
        self.completed.emit(self)


def _spin(low, high, value, suffix, decimals=1, step=1.):
    spin = QtWidgets.QDoubleSpinBox()
    spin.setRange(low, high)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)
    return spin


class CorrectionMixin:
    def _build_generator(self):
        self.correction_preview = None
        self.preview_worker = None
        self._preview_sig = UNSET
        self._preview_pending = False
        self._generator_open = False
        self._key_cache = {}
        self.correction_cancel = threading.Event()
        self.correction_timer = QtCore.QTimer(self)
        self.correction_timer.setSingleShot(True)
        self.correction_timer.setInterval(300)
        self.correction_timer.timeout.connect(self._start_preview)

        panel = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(panel)
        form.setContentsMargins(0, 2, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.fit_mode = QtWidgets.QComboBox()
        self.fit_mode.addItem("RAW 曲线（逐点）", "RAW")
        self.fit_mode.addItem("PEQ 滤波器", "PEQ")
        self.fit_count = QtWidgets.QSpinBox()
        self.fit_count.setRange(1, 20)
        self.fit_count.setValue(8)
        self.fit_count.setKeyboardTracking(False)
        self.fit_low, self.fit_high = _spin(20, 20000, 20, " Hz", 0), _spin(20, 20000, 20000, " Hz", 0)
        self.fit_strength = QtWidgets.QSpinBox()
        self.fit_strength.setRange(1, 100)
        self.fit_strength.setValue(100)
        self.fit_strength.setSuffix(" %")
        self.fit_strength.setKeyboardTracking(False)
        self.fit_boost, self.fit_cut = _spin(.1, 60, 12, " dB"), _spin(.1, 60, 24, " dB")
        self.fit_smoothing = QtWidgets.QComboBox()
        for title, value in SMOOTHING:
            self.fit_smoothing.addItem(title, value)
        self.fit_smoothing.setCurrentIndex(2)
        self.fit_align = QtWidgets.QCheckBox("按 1 kHz 对齐整体电平")
        self.fit_align.setChecked(True)
        band = QtWidgets.QWidget()
        band_layout = QtWidgets.QHBoxLayout(band)
        band_layout.setContentsMargins(0, 0, 0, 0)
        band_layout.setSpacing(4)
        band_layout.addWidget(self.fit_low, 1)
        band_layout.addWidget(QtWidgets.QLabel("–"))
        band_layout.addWidget(self.fit_high, 1)
        form.addRow("模式", self.fit_mode)
        form.addRow("PEQ 数量", self.fit_count)
        form.addRow("频段", band)
        form.addRow("强度", self.fit_strength)
        form.addRow("平滑", self.fit_smoothing)
        form.addRow("最大提升", self.fit_boost)
        form.addRow("最大衰减", self.fit_cut)
        form.addRow(self.fit_align)
        self.preview_status = label("", "muted")
        form.addRow(self.preview_status)
        self.apply_preview_button = button("应用到修正 EQ", self.apply_correction_preview, primary=True,
                                           tooltip="用预览替换当前修正 EQ；可以撤销")
        form.addRow(self.apply_preview_button)
        form.setRowVisible(self.fit_count, False)
        self.fit_mode.currentIndexChanged.connect(
            lambda: form.setRowVisible(self.fit_count, self.fit_mode.currentData() == "PEQ"))
        for spin in (self.fit_count, self.fit_low, self.fit_high, self.fit_strength, self.fit_boost, self.fit_cut):
            spin.valueChanged.connect(self.schedule_correction_preview)
        for combo in (self.fit_mode, self.fit_smoothing):
            combo.currentIndexChanged.connect(self.schedule_correction_preview)
        self.fit_align.toggled.connect(self.schedule_correction_preview)
        panel.hide()
        self.generator_panel = panel
        return panel

    # ---------------------------------------------------------------- state
    def generator_toggled(self, expanded):
        self._generator_open = bool(expanded)
        self.generator_panel.setVisible(self._generator_open)
        self.fit_target_button.setText(("▾ " if expanded else "▸ ")+"生成修正 EQ")
        if not expanded:
            self.discard_correction_preview()
        else:
            self.schedule_correction_preview()

    def start_fit(self):
        """Menu entry: open the inline generator and bring it into view."""
        if hasattr(self, "set_rail_expanded"):
            self.set_rail_expanded(True)
        self.cards["correction"].set_expanded(True)
        self.fit_target_button.setChecked(True)
        QtCore.QTimer.singleShot(0, self.rail, lambda: self.rail.ensureWidgetVisible(self.generator_panel, 0, 12))

    def correction_settings(self):
        low, high = self.fit_low.value(), self.fit_high.value()
        if low >= high:
            raise ValueError("起始频率必须低于结束频率")
        return self.fit_mode.currentData(), FitOptions(
            low, high, 1000 if self.fit_align.isChecked() else None, self.fit_strength.value()/100,
            self.fit_smoothing.currentData(), self.fit_boost.value(), self.fit_cut.value(), self.fit_count.value())

    def _key(self, curve):
        cached = self._key_cache.get(id(curve))
        if cached and cached[0] is curve:
            return cached[1]
        if len(self._key_cache) > 32:
            self._key_cache.clear()
        key = measurement_key(curve)
        self._key_cache[id(curve)] = (curve, key)
        return key

    def _correction_inputs(self):
        """Everything a preview depends on; None when measurement or target is missing."""
        a, b = self.measurement_combo.currentIndex(), self.target_combo.currentIndex()
        if not (0 <= a < len(self.measurements) and 0 <= b < len(self.targets)):
            return None
        try:
            mode, options = self.correction_settings()
        except ValueError as exc:
            return ("invalid", str(exc))
        context = self.session.context
        firmware = (self.firmware.sha256 if self.firmware and self.firmware.profiles else None,
                    (context.get("reference") or {}).get("sha256"),
                    json.dumps({k: context.get(k) for k in CONTEXT_KEYS}, sort_keys=True, default=str))
        return (self._key(self.measurements[a]), self._key(self.targets[b]), mode,
                json.dumps(options.__dict__, sort_keys=True, default=str), firmware)

    def schedule_correction_preview(self, *_):
        self._preview_sig = UNSET
        self._sync_correction_preview()

    def _sync_correction_preview(self):
        """Called at the start of every refresh; recomputes only when inputs changed."""
        if not getattr(self, "_generator_open", False):
            return
        signature = self._correction_inputs()
        if signature == self._preview_sig:
            return
        self._preview_sig = signature
        self.correction_preview = None
        self.correction_cancel.set()
        self.apply_preview_button.setEnabled(False)
        if signature is None:
            self._preview_message("先在 ①、② 选择原始频响与目标频响。")
            return
        if signature[0] == "invalid":
            self._preview_message(signature[1], warn=True)
            return
        slow = self.fit_mode.currentData() == "PEQ" and self.fit_count.value() > 12
        # PEQ time still grows with the count: ~0.3 s for 8 filters, a few seconds for 20.
        self._preview_message("正在计算预览…"+("（PEQ 数量较多，可能需要几秒）" if slow else ""))
        self.correction_timer.start()

    def _preview_message(self, text, warn=False):
        self.preview_status.setText(text)
        mark(self.preview_status, "muted", "warn" if warn else None)

    def _correction_source(self, index):
        original = copy.deepcopy(self.measurements[index])
        difference = self.difference(np.asarray(original.frequencies))
        if difference:
            original.spl_values = (np.asarray(original.spl_values)+difference["delta"]).tolist()
            original.title += " · 当前固件估计"
        return original

    def _start_preview(self):
        if not self._generator_open or self._preview_sig in (None, UNSET) or self._preview_sig[0] == "invalid":
            return
        if self.preview_worker is not None:
            self._preview_pending = True
            return
        try:
            original = self._correction_source(self.measurement_combo.currentIndex())
            mode, options = self.correction_settings()
        except (ValueError, KeyError, IndexError) as exc:
            self._preview_message(f"无法生成预览：{exc}", warn=True)
            return
        target = copy.deepcopy(self.targets[self.target_combo.currentIndex()])
        self.correction_cancel = cancelled = threading.Event()
        worker = PreviewWorker(lambda: fit_response(original, target, mode, options, cancelled),
                               self._preview_sig, self)
        worker.completed.connect(self._preview_finished, QtCore.Qt.ConnectionType.QueuedConnection)
        self.preview_worker = worker
        worker.start()

    def _preview_finished(self, worker):
        worker.wait()
        self.preview_worker = None
        value, error = worker.result
        worker.fn = None
        worker.deleteLater()
        self._preview_result(worker.signature, value, error)
        if self._preview_pending:
            self._preview_pending = False
            self._start_preview()

    def _preview_result(self, signature, value, error=None):
        if signature != self._preview_sig or not self._generator_open or isinstance(error, InterruptedError):
            return  # Stale or cancelled: inputs changed while it was running.
        if error:
            self._preview_message(f"预览未生成：{error}", warn=True)
            return
        doc, report = value
        self.correction_preview = (doc, report)
        worst = max(report["rates"].values(), key=lambda v: v["max_db"])
        size = f"RAW {len(doc.raw)} 点" if report["mode"] == "RAW" else f"PEQ {len(doc.filters)} 个"
        self._preview_message(f"预览：{size} · 最差 RMS {worst['rms_db']:.2f} / 最大 {worst['max_db']:.2f} dB"
                              "（相对平滑／限幅后的期望修正）")
        self.apply_preview_button.setEnabled(True)
        self.refresh()

    def apply_correction_preview(self):
        if not self.correction_preview:
            return
        doc, report = self.correction_preview
        self.correction_preview = None
        self.set_document(doc)
        self.fit_report = report
        self.apply_preview_button.setEnabled(False)
        self._preview_message("已应用到修正 EQ；修改设置会生成新的预览。")
        self.refresh()
        self.statusBar().showMessage("已应用生成的修正 EQ；可以撤销。")

    def discard_correction_preview(self, *_):
        self._preview_sig = UNSET
        self.correction_cancel.set()
        self.correction_timer.stop()
        had_preview = self.correction_preview is not None
        self.correction_preview = None
        self._preview_message("")
        if had_preview:
            self.refresh()

    def fit_measurements(self, original, target, mode, options):
        """Fit and replace the correction directly (scripted use and tests)."""
        if self.workers:
            return
        generation = self.edit_generation
        original, target = copy.deepcopy(original), copy.deepcopy(target)
        try:
            difference = self.difference(np.asarray(original.frequencies))
            if difference:
                original.spl_values = (np.asarray(original.spl_values)+difference["delta"]).tolist()
                original.title += " · 当前固件估计"
        except (ValueError, KeyError, IndexError) as exc:
            self.statusBar().showMessage(f"无法按当前固件估计生成修正：{exc}")
            return
        self.fit_cancel = threading.Event()

        def received(result):
            if generation != self.edit_generation:
                self.statusBar().showMessage("拟合期间输入发生变化，结果未覆盖当前编辑。")
                return
            doc, report = result
            self.set_document(doc)
            self.fit_report = report
            self.refresh()
        self._task(lambda: fit_response(original, target, mode, options, self.fit_cancel), received)
