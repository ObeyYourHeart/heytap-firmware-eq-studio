"""Local Qt studio with external EQ and validated firmware edit plans."""

import copy
import json
import threading
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from heytap_eq import metadata
from heytap_eq.adapters import inspect_firmware
from heytap_eq.clipboard import (
    MIME,
    PRESET_SCHEMA,
    copy_plan,
    decode_text,
    preset_payload,
    tuning_payload,
)
from heytap_eq.configuration import config, configurations, record_at, regions, tables_for
from heytap_eq.discovery import discover
from heytap_eq.dsp import correction, firmware_curve
from heytap_eq.eq_formats import Filter, dump_eq, load_eq
from heytap_eq.estimation import (
    builtin_reference,
    estimate_difference,
    measurement_key,
    reference_snapshot,
)
from heytap_eq.firmware_dsp import response as firmware_response
from heytap_eq.firmware_edit import export_firmware, make_plan
from heytap_eq.fitting import FitOptions, fit_response
from heytap_eq.flowmix import FlowmixClient
from heytap_eq.measurements import load_measurements
from heytap_eq.plot import DARK_STYLE
from heytap_eq.preferences import BrowserMemory, device_identity
from heytap_eq.service_profile import builtin_authorization
from heytap_eq.session import Session, atomic_json
from heytap_eq.web_plot import ResponsePlot


class Worker(QtCore.QThread):
    progress = QtCore.Signal(str)
    def __init__(self, fn, callback, network, parent):
        super().__init__(parent)
        self.fn = fn
        self.callback = callback
        self.network = network
        self.result = None

    def run(self):
        try:
            self.result = (self.fn(), None)
        except Exception as exc:
            self.result = (None, exc)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, recover=True, auto_path=None):
        super().__init__()
        self.setWindowTitle("HeyTap Firmware EQ Studio")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1380, 880)
        self.session = Session()
        self.firmware = None
        self.measurements = []
        self.targets = []
        self.sources = []
        self.device = None
        self.fit_cancel = threading.Event()
        self.edit_generation = 0
        self.fit_report = None
        self._restoring = False
        self._online_request = 0
        self._preview_document = None
        self.preview_timer = QtCore.QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(25)
        self.preview_timer.timeout.connect(self.refresh)
        self.workers = set()
        self.network_workers = set()
        self.flowmix_client = None
        default_path = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.AppDataLocation)
        self.auto_path = Path(auto_path) if auto_path else Path(default_path)/"recovery.heytap.json"
        self.memory = BrowserMemory(self.auto_path.parent/"browser-memory.json")
        self.desired_selection = self.memory.selection()
        self._setup()
        if recover and self.auto_path.exists():
            try:
                self.session.restore(self.auto_path)
                self.statusBar().showMessage("已恢复上次 EQ 工程；重新打开原固件后会核对 SHA-256。")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.statusBar().showMessage(f"恢复文件未加载：{exc}")
        self.restore_curves()
        self.restore_context_controls()
        self.refresh()

    def _action(self, toolbar, label, fn, shortcut=None):
        action = QtGui.QAction(label, self)
        action.triggered.connect(fn)
        if shortcut:
            action.setShortcut(shortcut)
        toolbar.addAction(action)
        return action

    def _setup(self):
        self.setStyleSheet(DARK_STYLE)
        toolbar = self.addToolBar("工程与调音")
        toolbar.setMovable(False)
        files = self.menuBar().addMenu("工程")
        for label, fn in (("新建工程", self.new_project), ("打开工程", self.open_project),
                          ("保存工程", self.save_project), ("导出 EQ 文本", self.export_eq)):
            self._action(files, label, fn, "Ctrl+S" if label == "保存工程" else None)
        self._action(toolbar, "打开固件", self.open_firmware)
        self._action(toolbar, "导入 EQ", self.import_eq)
        self.undo_action = self._action(toolbar, "撤销", self.undo, "Ctrl+Z")
        self.redo_action = self._action(toolbar, "重做", self.redo, "Ctrl+Shift+Z")
        toolbar.addSeparator()
        copy_button = QtWidgets.QToolButton()
        copy_button.setText("复制调音")
        copy_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        copy_button.clicked.connect(self.copy_tuning)
        copy_menu = QtWidgets.QMenu(copy_button)
        self._action(copy_menu, "复制完整固件预设", self.copy_preset)
        copy_button.setMenu(copy_menu)
        toolbar.addWidget(copy_button)
        self._action(toolbar, "粘贴调音", self.paste_tuning, "Ctrl+Shift+V")
        copy_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+C"), self)
        copy_shortcut.activated.connect(self.copy_tuning)
        firmware_menu = self.menuBar().addMenu("固件详情")
        self.firmware_actions = [
            self._action(toolbar, "拟合到固件预设", self.start_firmware_fit),
            self._action(toolbar, "导出固件", self.start_firmware_export),
            self._action(firmware_menu, "编辑固件信息", self.edit_metadata),
            self._action(firmware_menu, "清空固件修改", self.clear_firmware_edits),
        ]
        self._action(firmware_menu, "载入配置映射", self.import_mapping)
        self._action(firmware_menu, "候选结构扫描", self.scan_candidates)
        body = QtWidgets.QWidget()
        self.setCentralWidget(body)
        workspace = QtWidgets.QVBoxLayout(body)
        workspace.setContentsMargins(8, 6, 8, 6)
        workspace.setSpacing(5)
        self.selection_area = QtWidgets.QScrollArea()
        self.selection_area.setWidgetResizable(True)
        self.selection_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.selection_area.setMinimumHeight(130)
        self.selection_area.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Maximum)
        selection_body = QtWidgets.QWidget()
        self.selection_area.setWidget(selection_body)
        workspace.addWidget(self.selection_area)
        layout = QtWidgets.QVBoxLayout(selection_body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.firmware_label = QtWidgets.QLabel("未打开固件 · 可直接编辑 EQ")
        self.firmware_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.firmware_label)
        self.firmware_edits_label = QtWidgets.QLabel()
        self.firmware_edits_label.setWordWrap(True)
        layout.addWidget(self.firmware_edits_label)
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("调音基底"))
        self.preset_buttons = {}
        self.preset_button_layout = QtWidgets.QHBoxLayout()
        preset_row.addLayout(self.preset_button_layout)
        preset_row.addStretch()
        self.preset_combo = QtWidgets.QComboBox(self)
        self.preset_combo.hide()
        self.preset_combo.currentIndexChanged.connect(self.context_changed)
        advanced = QtWidgets.QPushButton("计算详情")
        advanced.setCheckable(True)
        preset_row.addWidget(advanced)
        side_toggle = QtWidgets.QPushButton("参数表")
        side_toggle.setCheckable(True)
        preset_row.addWidget(side_toggle)
        layout.addLayout(preset_row)
        self.advanced_controls = QtWidgets.QWidget()
        details = QtWidgets.QGridLayout(self.advanced_controls)
        details.setContentsMargins(0, 0, 0, 0)
        self.region_combo = QtWidgets.QComboBox()
        self.state_combo = QtWidgets.QComboBox()
        self.rate_combo = QtWidgets.QComboBox()
        self.rate_combo.addItems(["44100", "48000", "96000"])
        self.rate_combo.setCurrentText("48000")
        self.crossover_spin = QtWidgets.QDoubleSpinBox()
        self.crossover_spin.setRange(1000, 20000)
        self.crossover_spin.setValue(15000)
        self.crossover_spin.setSuffix(" Hz")
        self.gain_mode_combo = QtWidgets.QComboBox()
        self.reference_state_combo = QtWidgets.QComboBox()
        for title, key in (("仅滤波器差分", "none"), ("假定整体增益按 gain0", "gain0"),
                           ("假定整体增益按 gain1", "gain1")):
            self.gain_mode_combo.addItem(title, key)
        for col, title, widget in ((0, "区域", self.region_combo), (2, "内部状态", self.state_combo),
                                    (4, "计算采样率", self.rate_combo), (0, "双单元模型交接频率", self.crossover_spin),
                                    (2, "增益模型", self.gain_mode_combo)):
            row = 1 if widget in (self.crossover_spin, self.gain_mode_combo) else 0
            details.addWidget(QtWidgets.QLabel(title), row, col)
            details.addWidget(widget, row, col+1)
        for widget in (self.region_combo, self.state_combo, self.rate_combo, self.gain_mode_combo):
            widget.currentIndexChanged.connect(self.context_changed)
        self.crossover_spin.valueChanged.connect(self.context_changed)
        details.addWidget(QtWidgets.QLabel("测量参考状态"), 1, 4)
        details.addWidget(self.reference_state_combo, 1, 5)
        self.reference_state_combo.currentIndexChanged.connect(self.context_changed)
        self.advanced_controls.hide()
        advanced.toggled.connect(self.advanced_controls.setVisible)
        layout.addWidget(self.advanced_controls)
        selections = QtWidgets.QHBoxLayout()
        original_card = QtWidgets.QGroupBox("原始频响")
        original = QtWidgets.QGridLayout(original_card)
        original.setContentsMargins(8, 6, 8, 6)
        self.measurement_combo = QtWidgets.QComboBox()
        self.measurement_combo.setMinimumWidth(140)
        self.measurement_combo.currentIndexChanged.connect(self.curve_selection_changed)
        original.addWidget(self.measurement_combo, 0, 0, 1, 3)
        import_measurement = QtWidgets.QPushButton("导入文件")
        import_measurement.clicked.connect(self.import_measurements)
        original.addWidget(import_measurement, 0, 3)
        self.source_combo, self.brand_combo, self.headphone_combo = [QtWidgets.QComboBox() for _ in range(3)]
        for col, combo, placeholder in ((0, self.source_combo, "数据源"), (1, self.brand_combo, "搜索品牌"),
                                        (2, self.headphone_combo, "搜索型号")):
            combo.setEditable(True)
            combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
            combo.lineEdit().setPlaceholderText(placeholder)
            combo.completer().setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
            combo.completer().setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
            combo.setMinimumWidth(95)
            original.addWidget(combo, 1, col)
        refresh_online = QtWidgets.QPushButton("刷新")
        refresh_online.clicked.connect(self.connect_flowmix)
        original.addWidget(refresh_online, 1, 3)
        self.source_combo.activated.connect(self.source_chosen)
        self.brand_combo.activated.connect(self.brand_chosen)
        self.headphone_combo.activated.connect(self.load_online_measurements)
        self.online_controls = [self.source_combo, self.brand_combo, self.headphone_combo, refresh_online]
        selections.addWidget(original_card, 1)
        target_card = QtWidgets.QGroupBox("目标频响")
        target = QtWidgets.QGridLayout(target_card)
        target.setContentsMargins(8, 6, 8, 6)
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.setMinimumWidth(140)
        self.target_combo.currentIndexChanged.connect(self.curve_selection_changed)
        target.addWidget(self.target_combo, 0, 0, 1, 2)
        import_target = QtWidgets.QPushButton("导入文件")
        import_target.clicked.connect(self.import_target)
        target.addWidget(import_target, 0, 2)
        self.library_combo = QtWidgets.QComboBox()
        self.library_combo.setEditable(True)
        self.library_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.library_combo.lineEdit().setPlaceholderText("搜索目标曲线库")
        self.library_combo.completer().setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        self.library_combo.activated.connect(self.target_library_chosen)
        target.addWidget(self.library_combo, 1, 0)
        use_measurement = QtWidgets.QPushButton("使用原始曲线")
        use_measurement.clicked.connect(self.measurement_as_target)
        target.addWidget(use_measurement, 1, 1)
        fit_target = QtWidgets.QPushButton("生成修正 EQ")
        fit_target.clicked.connect(self.start_fit)
        target.addWidget(fit_target, 1, 2)
        selections.addWidget(target_card, 1)
        layout.addLayout(selections)
        reference_row = QtWidgets.QHBoxLayout()
        reference_row.addWidget(QtWidgets.QLabel("测量参考"))
        self.reference_label = QtWidgets.QLabel("未绑定参考固件")
        reference_row.addWidget(self.reference_label)
        reference_button = QtWidgets.QPushButton("选择参考固件")
        reference_button.clicked.connect(self.import_reference)
        reference_row.addWidget(reference_button)
        self.reference_preset_combo = QtWidgets.QComboBox()
        self.reference_preset_combo.currentIndexChanged.connect(self.context_changed)
        reference_row.addWidget(self.reference_preset_combo)
        self.reference_assumed = QtWidgets.QCheckBox("参考关系为假定")
        self.reference_assumed.setChecked(True)
        self.reference_assumed.toggled.connect(self.context_changed)
        reference_row.addWidget(self.reference_assumed)
        reference_row.addStretch()
        layout.addLayout(reference_row)
        layout = workspace
        visibility = QtWidgets.QHBoxLayout()
        digital_visibility = QtWidgets.QHBoxLayout()
        self.curve_checks = {}
        for key, title, checked in (("original", "原始实测", True), ("current", "当前固件估计", True),
                                     ("estimated", "编辑后估计", True), ("target", "目标频响", True),
                                     ("correction", "修正 EQ", False), ("dac1", "DAC1（输出1）", False),
                                     ("dac2", "DAC2（输出2）", False), ("planned", "待导出估计", True)):
            check = QtWidgets.QCheckBox(title)
            check.setChecked(checked)
            check.toggled.connect(self.visibility_changed)
            self.curve_checks[key] = check
            (digital_visibility if key in ('correction', 'dac1', 'dac2') else visibility).addWidget(check)
        visibility.addStretch()
        self.relative_check = QtWidgets.QCheckBox("相对声压")
        self.dac_mode_combo = QtWidgets.QComboBox()
        self.dac_mode_combo.addItem("DAC 滤波链", "chain")
        self.dac_mode_combo.addItem("DAC 相对参考差分", "difference")
        self.dac_mode_combo.currentIndexChanged.connect(self.visibility_changed)
        digital_visibility.addWidget(self.dac_mode_combo)
        digital_visibility.addStretch()
        self.relative_check.setChecked(True)
        self.relative_check.toggled.connect(self.visibility_changed)
        visibility.addWidget(self.relative_check)
        layout.addLayout(visibility)
        layout.addLayout(digital_visibility)
        split = QtWidgets.QSplitter()
        layout.addWidget(split, 1)
        self.tabs = QtWidgets.QTabWidget()
        split.addWidget(self.tabs)
        self.main_plot = self._plot("dB · 相对频响／EQ 增益")
        # Compatibility aliases refer to the same view, never two independent graphs.
        self.digital_plot = self.acoustic_plot = self.main_plot
        self.tabs.addTab(self.main_plot, "调音")
        self.metadata_text = QtWidgets.QPlainTextEdit()
        self.metadata_text.setReadOnly(True)
        self.tabs.addTab(self.metadata_text, "固件信息与修改计划")
        self.side = QtWidgets.QWidget()
        side = QtWidgets.QVBoxLayout(self.side)
        self.eq_label = QtWidgets.QLabel()
        side.addWidget(self.eq_label)
        self.filters_table = QtWidgets.QTableWidget(0, 6)
        self.filters_table.setHorizontalHeaderLabels(["启用", "ID", "类型", "频率 Hz", "增益 dB", "Q"])
        self.filters_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.filters_table.itemChanged.connect(self.edit_filter)
        side.addWidget(self.filters_table, 1)
        self.firmware_table = QtWidgets.QTableWidget(0, 5)
        self.firmware_table.setHorizontalHeaderLabels(["输出", "固件类型", "增益 dB", "频率 Hz", "Q"])
        self.firmware_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        side.addWidget(self.firmware_table, 1)
        self.gains_label = QtWidgets.QLabel()
        side.addWidget(self.gains_label)
        split.addWidget(self.side)
        self.side.hide()
        side_toggle.toggled.connect(self.side.setVisible)
        split.setSizes([1050, 330])
        footer = QtWidgets.QHBoxLayout()
        self.quality_label = QtWidgets.QLabel()
        self.quality_label.setWordWrap(True)
        footer.addWidget(self.quality_label, 1)
        cancel = QtWidgets.QPushButton("取消计算")
        cancel.clicked.connect(self.cancel_fit)
        footer.addWidget(cancel)
        layout.addLayout(footer)
        self.main_plot.editRequested.connect(self.editor_event)
        self.main_plot.error.connect(lambda message: self.statusBar().showMessage(message))
        self._peq_map = []
        self.reset_plots()
        self.statusBar().showMessage("选择原始与目标频响，编辑修正，再拟合到目标固件预设。")

    def _plot(self, label):
        return ResponsePlot(label)

    def current_key(self):
        return self.preset_combo.currentData()

    def configure_firmware(self):
        bank = self.firmware.profiles if self.firmware else None
        while self.preset_button_layout.count():
            item = self.preset_button_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.preset_buttons = {}
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.region_combo.blockSignals(True)
        self.region_combo.clear()
        self.state_combo.blockSignals(True)
        self.state_combo.clear()
        if bank:
            for preset in configurations(bank):
                self.preset_combo.addItem(preset['title'], preset['key'])
                button = QtWidgets.QPushButton(preset['title'])
                button.setCheckable(True)
                button.clicked.connect(lambda checked=False, key=preset['key']:
                    self.preset_combo.setCurrentIndex(self.preset_combo.findData(key)))
                self.preset_buttons[preset['key']] = button
                self.preset_button_layout.addWidget(button)
            self.region_combo.addItems(regions(bank))
            self.state_combo.addItems(['内部状态 '+str(s) for s in bank['configuration']['states']])
        for combo in (self.preset_combo, self.region_combo, self.state_combo):
            combo.blockSignals(False)
        self.restore_context_controls()

    def restore_context_controls(self):
        self._restoring = True
        context = self.session.context
        index = self.preset_combo.findData(context.get('preset'))
        self.preset_combo.setCurrentIndex(index if index >= 0 else (0 if self.preset_combo.count() else -1))
        self.region_combo.setCurrentText(context.get('region', 'other'))
        self.state_combo.setCurrentIndex(min(int(context.get('state', 0)), max(0, self.state_combo.count()-1)))
        self.rate_combo.setCurrentText(str(context.get('rate', 48000)))
        self.crossover_spin.setValue(float(context.get('crossover', 15000)))
        self.gain_mode_combo.setCurrentIndex(max(0, self.gain_mode_combo.findData(context.get('gain_mode', 'none'))))
        self.reference_assumed.setChecked(context.get('reference_assumed', True))
        reference = context.get('reference')
        self.reference_preset_combo.clear()
        self.reference_state_combo.clear()
        if reference:
            self.reference_label.setText(reference.get('title', '自定义参考'))
            for preset in reference.get('presets', []):
                self.reference_preset_combo.addItem(preset['title'], preset['key'])
            self.reference_preset_combo.setCurrentIndex(max(0, self.reference_preset_combo.findData(
                context.get('reference_preset', '丹拿原声'))))
            ordinary = next((p for p in reference['presets'] if not p.get('special')), None)
            self.reference_state_combo.addItems([str(s) for s in ordinary.get('states', [])] if ordinary else ['特殊状态'])
            self.reference_state_combo.setCurrentIndex(min(int(context.get('reference_state', 0)),
                                                          max(0, self.reference_state_combo.count()-1)))
        else:
            self.reference_label.setText('未绑定参考固件')
        for key, check in self.curve_checks.items():
            check.setChecked(self.session.view_options.get(key, check.isChecked()))
        self.relative_check.setChecked(self.session.view_options.get('relative', True))
        self.dac_mode_combo.setCurrentIndex(max(0, self.dac_mode_combo.findData(self.session.view_options.get('dac_mode', 'chain'))))
        self._restoring = False

    def context_changed(self, *_):
        if self._restoring or not hasattr(self, 'main_plot'):
            return
        special = bool(self.firmware and self.firmware.profiles and self.current_key()
                       and config(self.firmware.profiles, self.current_key()).get('special'))
        changes = {'preset': self.current_key(), 'region': self.region_combo.currentText() or 'other',
            'state': 0 if special else max(0, self.state_combo.currentIndex()), 'rate': int(self.rate_combo.currentText()),
            'reference_preset': self.reference_preset_combo.currentData(),
            'reference_assumed': self.reference_assumed.isChecked(), 'crossover': self.crossover_spin.value(),
            'gain_mode': self.gain_mode_combo.currentData(), 'reference_state': max(0, self.reference_state_combo.currentIndex())}
        index = self.measurement_combo.currentIndex()
        if 0 <= index < len(self.measurements) and self.session.context.get('reference'):
            bindings = copy.deepcopy(self.session.context.get('reference_bindings', {}))
            bindings[measurement_key(self.measurements[index])] = {
                **{k: self.session.context[k] for k in ('reference',) if k in self.session.context},
                **{k: changes[k] for k in ('reference_preset', 'reference_state', 'reference_assumed')}}
            changes['reference_bindings'] = bindings
        self.session.set_context(changes)
        self._changed()

    def visibility_changed(self, *_):
        if self._restoring or not hasattr(self, 'main_plot'):
            return
        self.session.view_options = {key: c.isChecked() for key, c in self.curve_checks.items()}
        self.session.view_options['relative'] = self.relative_check.isChecked()
        self.session.view_options['dac_mode'] = self.dac_mode_combo.currentData()
        self.refresh()
        try:
            self.session.save(self.auto_path)
        except OSError as exc:
            self.statusBar().showMessage(f'显示设置未保存：{exc}')

    def reset_plots(self, *_):
        if not hasattr(self, 'main_plot'):
            return
        self.main_plot.reset_range(-24, 24, 5)
        self.refresh()

    def copy_tuning(self):
        payload = tuning_payload(self.session)
        self._set_clipboard(payload)
        self.statusBar().showMessage('已复制完整调音，包含 RAW／PEQ 与测量、目标、参考关联。')

    def _set_clipboard(self, payload):
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        data = QtCore.QMimeData()
        data.setData(MIME, text.encode('utf-8'))
        data.setText(text)
        QtWidgets.QApplication.clipboard().setMimeData(data)

    def copy_preset(self):
        if not self.firmware or not self.firmware.profiles or not self.current_key():
            self.statusBar().showMessage('先打开固件并选择要复制的配置。')
            return
        payload = preset_payload(self.firmware, self.current_key(), self.session.firmware_plans)
        self._set_clipboard(payload)
        self.statusBar().showMessage(f"已复制 {payload['title']} 的完整配置：{len(payload['records'])} 条记录。")

    def paste_tuning(self):
        try:
            mime = QtWidgets.QApplication.clipboard().mimeData()
            text = bytes(mime.data(MIME)).decode('utf-8') if mime.hasFormat(MIME) else mime.text()
            payload = decode_text(text)
            if payload['schema'] == PRESET_SCHEMA:
                if not self.firmware or not self.firmware.profiles:
                    raise ValueError('打开目标固件后才能粘贴完整预设；通用 EQ 文本可直接粘贴。')
                presets = [p for p in configurations(self.firmware.profiles)
                           if bool(p.get('special')) == bool(payload.get('special'))]
                labels = [p['title'] for p in presets]
                initial = next((i for i,p in enumerate(presets) if p['key'] == self.current_key()), 0)
                label, ok = QtWidgets.QInputDialog.getItem(self, '粘贴完整预设', '写入目标配置', labels, initial, False)
                if not ok:
                    return
                destination = presets[labels.index(label)]['key']
                self.session.stage_plan(copy_plan(self.firmware, payload, destination))
                self.preset_combo.blockSignals(True)
                self.preset_combo.setCurrentIndex(self.preset_combo.findData(destination))
                self.preset_combo.blockSignals(False)
            else:
                self.session.paste_tuning(payload['state'])
                self.restore_curves()
                self.restore_context_controls()
            self._preview_document = None
            self._changed()
            self.statusBar().showMessage('已粘贴；本次操作可一步撤销。')
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            self.statusBar().showMessage(f'粘贴未完成：{exc}')

    def import_mapping(self):
        if not self.firmware:
            self.statusBar().showMessage('先打开固件，再载入它的配置映射。')
            return
        path = self._choose('载入配置映射', '配置映射 (*.json)')
        if path:
            firmware_path = self.firmware.path
            self._task(lambda: inspect_firmware(firmware_path, json.loads(Path(path).read_text(encoding='utf-8'))),
                       self.set_firmware)

    def import_reference(self):
        path = self._choose('选择测量所对应的参考固件')
        if path:
            def received(firmware):
                if not firmware.profiles:
                    raise ValueError('参考固件尚未识别 EQ 映射')
                changes = {'reference': reference_snapshot(firmware), 'reference_assumed': True,
                           'reference_preset': configurations(firmware.profiles)[0]['key'], 'reference_state': 0}
                index = self.measurement_combo.currentIndex()
                if 0 <= index < len(self.measurements):
                    bindings = copy.deepcopy(self.session.context.get('reference_bindings', {}))
                    bindings[measurement_key(self.measurements[index])] = copy.deepcopy(changes)
                    changes['reference_bindings'] = bindings
                self.session.set_context(changes, '绑定参考固件')
                self.restore_context_controls()
                self._changed()
            mapping = copy.deepcopy(self.session.context.get('mapping'))
            self._task(lambda: inspect_firmware(path, mapping), received)

    def difference(self, frequency, plans=(), current_key=None):
        context = self.session.context
        if not (self.firmware and self.firmware.profiles and context.get('reference') and self.current_key()):
            return None
        return estimate_difference(self.firmware, context['reference'], current_key or self.current_key(),
            self.reference_preset_combo.currentData(), frequency, self.region_combo.currentText(),
            0 if config(self.firmware.profiles, current_key or self.current_key()).get('special') else max(0, self.state_combo.currentIndex()),
            int(self.rate_combo.currentText()), self.crossover_spin.value(), self.gain_mode_combo.currentData(), plans,
            max(0, self.reference_state_combo.currentIndex()))

    def _choose(self, title, pattern="所有文件 (*)", save=False):
        method = QtWidgets.QFileDialog.getSaveFileName if save else QtWidgets.QFileDialog.getOpenFileName
        return method(self, title, "", pattern)[0]

    def _task(self, fn, callback, network=False, with_progress=False):
        workers = self.network_workers if network else self.workers
        if workers and not network:
            self.statusBar().showMessage("正在读取文件，请等待当前任务完成。")
            return
        worker = Worker(fn, callback, network, self)
        if with_progress:
            worker.fn = lambda: fn(worker.progress.emit)
        worker.progress.connect(self.statusBar().showMessage)
        workers.add(worker)
        if network:
            for control in self.online_controls:
                control.setEnabled(False)
        self.statusBar().showMessage("正在读取并验证…")

        worker.finished.connect(self.worker_finished, QtCore.Qt.ConnectionType.QueuedConnection)
        worker.start()

    @QtCore.Slot()
    def worker_finished(self):
        worker = self.sender()
        # finished can precede native TLS cleanup; join before freeing the wrapper.
        worker.wait()
        workers = self.network_workers if worker.network else self.workers
        workers.discard(worker)
        if worker.network:
            for control in self.online_controls:
                control.setEnabled(not workers)
        try:
            value, error = worker.result
            if error:
                raise error
            worker.callback(value)
        except Exception as exc:
            self.statusBar().showMessage(f"操作未完成：{exc}")
            self.statusBar().setToolTip(str(exc))
        finally:
            worker.fn = None
            worker.callback = None
            worker.deleteLater()

    def connect_flowmix(self):
        def connected(auth):
            self.flowmix_client = FlowmixClient(auth, self.auto_path.parent/"flowmix-cache")
            self._task(self.flowmix_client.sources, self.set_online_sources, network=True)
            self._task(self.flowmix_client.targets, self.set_target_library, network=True)
        def prepare():
            auth = builtin_authorization(self.auto_path.parent)
            if not auth:
                raise ValueError("此包尚未配置测量接口认证；离线导入与拟合可直接使用。")
            return auth
        self._task(prepare, connected, network=True)

    def fill_online(self, combo, entries, preferred=None):
        combo.blockSignals(True)
        combo.clear()
        for entry in entries:
            combo.addItem(entry["display"], entry["name"])
        combo.setCurrentIndex(combo.findData(preferred) if preferred else -1)
        combo.blockSignals(False)

    def remember_browser(self, *_):
        selection = {"source": self.source_combo.currentData(),
                     "brand": self.brand_combo.currentData(),
                     "headphone": self.headphone_combo.currentData()}
        self.desired_selection = selection
        self.session.online_selection = selection
        try:
            self.memory.remember(selection, self.device["key"] if self.device else None)
        except OSError as exc:
            self.statusBar().showMessage(f"选择未保存：{exc}")
        try:
            self.session.save(self.auto_path)
        except OSError as exc:
            self.statusBar().showMessage(f"工程未保存：{exc}")

    def source_chosen(self, *_):
        self.brand_combo.clear()
        self.headphone_combo.clear()
        self.remember_browser()
        self.load_online_brands()

    def brand_chosen(self, *_):
        self.headphone_combo.clear()
        self.remember_browser()
        self.load_online_headphones()

    def set_online_sources(self, entries):
        self.sources = entries
        self.fill_online(self.source_combo, entries, self.desired_selection.get("source"))
        if self.source_combo.currentData():
            self.load_online_brands()
        elif self.device and "model" in self.device and not self.desired_selection:
            self.match_firmware_device()
        else:
            self.statusBar().showMessage("在线来源已载入；请选择来源、品牌与型号。")

    def match_firmware_device(self):
        if not self.flowmix_client or not self.device or "model" not in self.device:
            return
        identity = dict(self.device)
        def matched(selection):
            if not self.device or self.device["key"] != identity["key"]:
                return
            if selection:
                self.desired_selection = selection
                self.fill_online(self.source_combo, self.sources, selection["source"])
                self.load_online_brands()
            else:
                self.statusBar().showMessage("未找到唯一的精确型号匹配，请手动选择测量。")
        self._task(lambda: self.flowmix_client.match_device(identity, self.sources), matched, network=True)

    def load_online_brands(self, *_):
        source = self.source_combo.currentData()
        if self.flowmix_client and source:
            self.brand_combo.clear()
            self.headphone_combo.clear()
            def received(entries):
                if self.source_combo.currentData() == source:
                    self.set_online_brands(entries)
            self._task(lambda: self.flowmix_client.brands(source), received, network=True)

    def set_online_brands(self, entries):
        self.fill_online(self.brand_combo, entries, self.desired_selection.get("brand"))
        if self.brand_combo.currentData():
            self.load_online_headphones()

    def load_online_headphones(self, *_):
        source, brand = self.source_combo.currentData(), self.brand_combo.currentData()
        if self.flowmix_client and source and brand:
            self.headphone_combo.clear()
            def received(entries):
                if (self.source_combo.currentData(), self.brand_combo.currentData()) == (source, brand):
                    self.set_online_headphones(entries)
            self._task(lambda: self.flowmix_client.headphones(source, brand), received, network=True)

    def set_online_headphones(self, entries):
        self.fill_online(self.headphone_combo, entries, self.desired_selection.get("headphone"))
        if self.headphone_combo.currentData():
            self.remember_browser()
            self.load_online_measurements()
        origin = "离线缓存" if self.flowmix_client.from_cache else "在线"
        self.statusBar().showMessage(f"{origin}型号索引已载入；选择型号后自动载入测量。")

    def load_online_measurements(self, *_):
        source, brand = self.source_combo.currentData(), self.brand_combo.currentData()
        name = self.headphone_combo.currentData()
        if self.flowmix_client and source and brand and name:
            self.remember_browser()
            def received(curves):
                if (source, brand, name) != (self.source_combo.currentData(), self.brand_combo.currentData(),
                                             self.headphone_combo.currentData()):
                    return
                self.set_measurements(curves)
                self.tabs.setCurrentIndex(0)
                origin = "离线缓存" if self.flowmix_client.from_cache else "在线"
                self.statusBar().showMessage(f"已载入{origin}测量 {len(curves)} 条。")
            self._task(lambda: self.flowmix_client.measurements(source, brand, name), received, network=True)
        else:
            self.statusBar().showMessage("请先选择来源、品牌与型号。")

    def set_target_library(self, entries):
        self.fill_online(self.library_combo, entries)

    def target_library_chosen(self, *_):
        identifier = self.library_combo.currentData()
        if self.flowmix_client and identifier:
            def received(curves):
                if self.library_combo.currentData() == identifier:
                    self.set_targets(curves)
            self._task(lambda: self.flowmix_client.target(identifier), received, network=True)

    def import_target(self):
        path = self._choose("导入目标频响", "测量 (*.csv *.txt *.frd *.json *.har)")
        if path:
            self._task(lambda: load_measurements(path), self.set_targets)

    def measurement_as_target(self):
        index = self.measurement_combo.currentIndex()
        if index >= 0:
            self.set_targets([copy.deepcopy(self.measurements[index])])

    def set_targets(self, curves):
        self.targets = list(self.targets)
        for curve in curves:
            if curve not in self.targets:
                self.targets.append(curve)
        self.fill_curves(self.target_combo, self.targets,
                         self.targets.index(curves[0]) if curves else -1)
        self.curve_selection_changed()
        self.tabs.setCurrentIndex(0)

    def fill_curves(self, combo, curves, index):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems([c.title+" · "+c.source for c in curves])
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def restore_curves(self):
        self.measurements = self.session.measurements
        self.targets = self.session.targets
        self.fill_curves(self.measurement_combo, self.measurements, self.session.measurement_index)
        self.fill_curves(self.target_combo, self.targets, self.session.target_index)
        if self.session.online_selection:
            self.desired_selection = dict(self.session.online_selection)

    def curve_selection_changed(self, *_):
        if self._restoring:
            return
        index = self.measurement_combo.currentIndex()
        binding = self.session.context.get('reference_bindings', {}).get(measurement_key(self.measurements[index])) \
            if 0 <= index < len(self.measurements) and index != self.session.measurement_index else None
        self.session.set_curves(self.measurements, self.targets, index, self.target_combo.currentIndex(), binding)
        if binding:
            self.restore_context_controls()
        self._changed()

    def cancel_fit(self):
        self.fit_cancel.set()
        self.statusBar().showMessage("正在取消当前拟合或封包任务…")

    def start_firmware_fit(self):
        if not self.firmware or not self.firmware.profiles or self.workers:
            return
        if not self.session.document.raw and not self.session.document.filters:
            self.statusBar().showMessage("先导入、编辑或生成修正 EQ。")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("拟合到固件完整预设")
        form = QtWidgets.QFormLayout(dialog)
        destination = QtWidgets.QComboBox()
        baseline = self.current_key()
        source_config = config(self.firmware.profiles, baseline)
        for preset in configurations(self.firmware.profiles):
            if len(preset["indices"]) == len(source_config["indices"]):
                destination.addItem(preset["title"], preset["key"])
        preferred = "丹拿高解析" if baseline == "丹拿原声" else baseline
        destination.setCurrentIndex(max(0, destination.findData(preferred)))
        preamp = QtWidgets.QDoubleSpinBox()
        preamp.setRange(-24, 0)
        preamp.setValue(self.session.context.get('preamp_db', 0.))
        preamp.setSuffix(" dB")
        form.addRow("替换预设", destination)
        form.addRow("整体衰减", preamp)
        count = len(self.firmware.profiles["tables"])*len(source_config["indices"])
        note = QtWidgets.QLabel(f"以当前 {source_config['title']} 为基底，加上当前修正 EQ。\n"
                               f"同时处理所有输出、区域和对应状态，共 {count} 条；保留基底 HP/LP/AP。\n"
                               "完成后加入修改计划；可以继续为其他预设准备不同 EQ。")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.fit_firmware_preset(destination.currentData(), preamp.value())

    def fit_firmware_preset(self, destination, preamp=0):
        if not self.firmware or not self.firmware.profiles or self.workers:
            return
        firmware, doc = self.firmware, copy.deepcopy(self.session.document)
        baseline = self.current_key()
        self.session.set_context({'preamp_db': preamp}, '调整整体衰减')
        generation = self.edit_generation
        self.fit_cancel = threading.Event()
        cancelled = self.fit_cancel
        def received(plan):
            if generation != self.edit_generation:
                self.statusBar().showMessage("拟合期间输入已变化，结果未加入当前固件计划。")
                return
            self.session.stage_plan(plan)
            self._changed()
            maximum = max(m["max_db"] for r in plan["records"] for m in r["record"]["metrics"].values())
            self.statusBar().showMessage(f"{destination} 已加入计划：{len(plan['records'])} 条记录，最大误差 {maximum:.3f} dB。")
        self._task(lambda progress: make_plan(firmware, doc, destination, preamp, cancelled, progress, baseline=baseline),
                   received, with_progress=True)

    def edit_metadata(self):
        if not self.firmware or not self.firmware.profiles:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("编辑固件信息")
        dialog.resize(850, 650)
        layout = QtWidgets.QVBoxLayout(dialog)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(body)
        inputs = {}
        known = metadata.fields(self.firmware.package)
        for field in known:
            value = self.session.metadata_edits.get(field["id"], field["value"])
            entry = QtWidgets.QLineEdit(value)
            entry.setReadOnly(not field.get("editable", True))
            if field.get("editable", True):
                inputs[field["id"]] = entry
            label = field["label"]
            if field["kind"] in ("text", "fixed_text"):
                label += f"（最多 {field['width']} 字节）"
            form.addRow(label, entry)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        note = QtWidgets.QLabel("文本按原字段容量保存；校验值和长度由封包自动计算。软件版本会同步 SW_VER 和运行时 getter。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        edits = {f["id"]: inputs[f["id"]].text() for f in known if f["id"] in inputs
                 and inputs[f["id"]].text() != f["value"]}
        try:
            self.set_metadata_edits(edits)
        except (ValueError, UnicodeError) as exc:
            self.statusBar().showMessage(f"固件信息未应用：{exc}")

    def set_metadata_edits(self, edits):
        if not self.firmware or not self.firmware.profiles:
            raise ValueError("请先打开已确认的固件。")
        metadata.apply_raw(self.firmware.package, bytearray(self.firmware.package["raw"]), edits)
        self.session.set_metadata(edits)
        self._changed()

    def clear_firmware_edits(self):
        self.session.clear_firmware_edits()
        self._changed()

    def start_firmware_export(self):
        if not self.firmware or self.workers:
            return
        if not self.session.firmware_plans and not self.session.metadata_edits:
            self.statusBar().showMessage("先拟合到固件预设，或编辑固件信息。")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("导出固件")
        form = QtWidgets.QFormLayout(dialog)
        values = [v for p in self.session.firmware_plans for r in p["records"]
                  for v in r["record"]["metrics"].values()]
        summary = "仅修改固件信息" if not values else (
            f"已准备 {len(self.session.firmware_plans)} 个预设；三采样率最差 RMS "
            f"{max(v['rms_db'] for v in values):.3f} / 最大 {max(v['max_db'] for v in values):.3f} dB")
        note = QtWidgets.QLabel(summary+"。导出时按实际写入参数重新计算误差。")
        note.setWordWrap(True)
        form.addRow(note)
        rms, peak = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for spin, limit, value in ((rms, 20, .45), (peak, 60, 1.5)):
            spin.setRange(.01, limit)
            spin.setDecimals(2)
            spin.setValue(value)
            spin.setSuffix(" dB")
            spin.setEnabled(bool(values))
        form.addRow("允许 RMS 误差", rms)
        form.addRow("允许最大误差", peak)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        original = Path(self.firmware.path)
        output = QtWidgets.QFileDialog.getSaveFileName(self, "导出修改后的固件",
                  str(original.with_name(original.stem+"-edited.bin")), "固件 (*.bin);;所有文件 (*)")[0]
        if output:
            self.export_to_path(output, rms.value(), peak.value())

    def export_to_path(self, output, max_rms=.45, max_error=1.5):
        if not self.firmware or self.workers:
            return
        firmware = self.firmware
        plans, edits = copy.deepcopy(self.session.firmware_plans), dict(self.session.metadata_edits)
        self.fit_cancel = threading.Event()
        cancelled = self.fit_cancel
        def received(report):
            self.last_export_report = report
            try:
                atomic_json(str(output)+".report.json", report)
                self.statusBar().showMessage(f"已导出 {Path(output).name}，并保存逐路径验证报告。")
            except OSError as exc:
                self.statusBar().showMessage(f"固件已导出，报告保存失败：{exc}")
        self._task(lambda: export_firmware(firmware, plans, edits, output, cancelled, max_rms, max_error), received)

    def start_fit(self):
        a, b = self.measurement_combo.currentIndex(), self.target_combo.currentIndex()
        if a < 0 or b < 0:
            self.statusBar().showMessage("请选择原始频响与目标频响。")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("频响生成修正 EQ")
        form = QtWidgets.QFormLayout(dialog)
        mode = QtWidgets.QComboBox()
        mode.addItems(["RAW", "PEQ"])
        low, high = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for spin, value in ((low, 20), (high, 20000)):
            spin.setRange(20, 20000)
            spin.setValue(value)
        count = QtWidgets.QSpinBox()
        count.setRange(1, 20)
        count.setValue(8)
        strength = QtWidgets.QSpinBox()
        strength.setRange(1, 100)
        strength.setValue(100)
        boost = QtWidgets.QDoubleSpinBox()
        boost.setRange(.1, 60)
        boost.setValue(12)
        align = QtWidgets.QCheckBox("按 1 kHz 对齐整体电平")
        align.setChecked(True)
        for title, widget in (("模式", mode), ("起始 Hz", low), ("结束 Hz", high),
                               ("PEQ 数量", count), ("强度 %", strength), ("最大提升 dB", boost)):
            form.addRow(title, widget)
        form.addRow(align)
        note = QtWidgets.QLabel("生成结果替换当前修正 EQ，可撤销；不需要打开固件。")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        options = FitOptions(low.value(), high.value(), 1000 if align.isChecked() else None,
                             strength.value()/100, max_boost=boost.value(), filters=count.value())
        self.fit_measurements(self.measurements[a], self.targets[b], mode.currentText(), options)

    def fit_measurements(self, original, target, mode, options):
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
            text = " · ".join(f"{rate} Hz: RMS {v['rms_db']:.3f} / 最大 {v['max_db']:.3f} dB"
                               for rate, v in report["rates"].items())
            self.quality_label.setText("相对于平滑/限幅后的期望修正："+text)
            self.tabs.setCurrentIndex(0)
        self._task(lambda: fit_response(original, target, mode, options, self.fit_cancel), received)

    def open_firmware(self):
        path = self._choose("打开固件")
        if path:
            mapping = copy.deepcopy(self.session.context.get("mapping"))
            self._task(lambda: inspect_firmware(path, mapping), self.set_firmware)

    def set_firmware(self, firmware):
        if self.session.firmware_sha256 not in (None, firmware.sha256):
            raise ValueError("工程绑定另一个固件；请打开原固件，或新建工程后切换。")
        bound_selection = dict(self.session.online_selection) if self.session.firmware_sha256 == firmware.sha256 else {}
        self.firmware = firmware
        self.session.firmware_sha256 = firmware.sha256
        self.session.firmware_path = firmware.path
        if firmware.mapping:
            self.session.context["mapping"] = firmware.mapping
        if firmware.profiles and not self.session.context.get("reference") and firmware.package["summary"]["product_id"] == "06EC10":
            self.session.context.update({"reference": builtin_reference(), "reference_preset": "丹拿原声",
                                         "reference_assumed": True})
        self.configure_firmware()
        self.metadata_text.setPlainText(json.dumps(firmware.package["summary"], ensure_ascii=False, indent=2))
        self.device = device_identity(firmware)
        self.desired_selection = self.memory.selection(self.device["key"]) or bound_selection
        self._changed()
        self.statusBar().showMessage(firmware.recognition)
        if self.flowmix_client:
            self.set_online_sources(self.sources)
        else:
            self.connect_flowmix()

    def scan_candidates(self):
        if self.firmware:
            self._task(lambda: discover(self.firmware.package["raw"]), self.show_candidates)

    def show_candidates(self, result):
        self.metadata_text.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.tabs.setCurrentWidget(self.metadata_text)
        self.statusBar().showMessage("仅显示候选参数和可能指针；没有赋予名称或写入权限。")

    def import_eq(self):
        path = self._choose("导入 EQ", "EQ 文本（Flowmix / GraphicEQ / Equalizer APO）(*.txt);;所有文件 (*)")
        if path:
            self._task(lambda: load_eq(path), self.set_document)

    def set_document(self, doc):
        self._preview_document = None
        self.session.replace(doc)
        self._changed()

    def import_measurements(self):
        path = self._choose("导入人工耳测量", "测量 (*.csv *.txt *.frd *.json *.har)")
        if path:
            self._task(lambda: load_measurements(path), self.set_measurements)

    def set_measurements(self, curves):
        self.measurements = list(self.measurements)
        for curve in curves:
            if curve not in self.measurements:
                self.measurements.append(curve)
        self.fill_curves(self.measurement_combo, self.measurements,
                         self.measurements.index(curves[0]) if curves else -1)
        self.curve_selection_changed()
        self.statusBar().showMessage(f"已导入 {len(curves)} 条实测。")

    def new_project(self):
        if self.workers:
            return
        self.session = Session()
        self._preview_document = None
        self.firmware = None
        self.device = None
        self.restore_curves()
        self.configure_firmware()
        self.metadata_text.clear()
        self._changed()

    def open_project(self):
        path = self._choose("打开工程", "HeyTap 工程 (*.json)")
        if path:
            try:
                self.session.restore(path, self.firmware.sha256 if self.firmware else None)
                self.restore_curves()
                self.restore_context_controls()
                self._changed()
                self.reset_plots()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.statusBar().showMessage(f"工程未加载：{exc}")

    def save_project(self):
        path = self._choose("保存工程", "HeyTap 工程 (*.heytap.json)", save=True)
        if path:
            try:
                self.session.save(path)
                self.statusBar().showMessage("工程已保存")
            except (ValueError, OSError) as exc:
                self.statusBar().showMessage(f"保存失败：{exc}")

    def export_eq(self):
        path = self._choose("导出 Flowmix EQ", "EQ 文本 (*.txt)", save=True)
        if path:
            try:
                Path(path).write_text(dump_eq(self.session.document), encoding="utf-8")
                self.statusBar().showMessage("EQ 文本已导出")
            except (ValueError, OSError) as exc:
                self.statusBar().showMessage(f"导出失败：{exc}")

    def editor_event(self, event):
        """Preview a gesture without checkpoints; commit only its final event."""
        try:
            op = event["op"]
            if op == "begin":
                self._preview_document = copy.deepcopy(self.session.document)
                self.edit_generation += 1
                self.fit_report = None
                return
            doc = copy.deepcopy(self.session.document)
            if op == "add":
                new_id = max((f.id for f in doc.filters), default=0)+1
                doc.filters.append(Filter(new_id, float(event["frequency"]),
                                          float(event["gain"]), .7))
            elif op in ("filter", "raw", "delete"):
                index = event["index"]
                if type(index) is not int or index < 0:
                    raise ValueError("Invalid editor index")
                if op == "filter":
                    data = event["filter"]
                    original = doc.filters[index]
                    doc.filters[index] = Filter(original.id, float(data["frequency"]),
                        float(data["gain"]), float(data["q"]), data["kind"], data["enabled"])
                elif op == "raw":
                    doc.raw[index][1] = float(event["gain"])
                else:
                    del doc.filters[index]
            else:
                raise ValueError("Unknown editor operation")
            doc.validate()
            phase = event.get("phase", "end")
            if phase not in ("change", "end"):
                raise ValueError("Invalid edit phase")
            if phase == "change":
                self._preview_document = doc
                if not self.preview_timer.isActive():
                    self.preview_timer.start()
            else:
                self.preview_timer.stop()
                self.set_document(doc)
        except (ValueError, IndexError, KeyError, TypeError, OverflowError) as exc:
            self._preview_document = None
            self.preview_timer.stop()
            self.refresh()
            self.statusBar().showMessage(f"图形参数未应用：{exc}")

    def add_filter(self):
        doc = copy.deepcopy(self.session.document)
        new_id = max((f.id for f in doc.filters), default=0)+1
        frequency = 1000.
        while any(abs(f.frequency-frequency) < 1 for f in doc.filters) and frequency < 16000:
            frequency *= 1.25
        doc.filters.append(Filter(new_id, frequency, 0, .7))
        self.set_document(doc)

    def remove_filter(self):
        row = self.filters_table.currentRow()
        if row >= 0:
            doc = copy.deepcopy(self.session.document)
            del doc.filters[row]
            self.set_document(doc)

    def edit_filter(self, item):
        row = item.row()
        doc = copy.deepcopy(self.session.document)
        try:
            entries = [self.filters_table.item(row, i) for i in range(6)]
            doc.filters[row] = Filter(int(entries[1].text()), float(entries[3].text()),
                                      float(entries[4].text()), float(entries[5].text()),
                                      entries[2].text().upper(), entries[0].checkState() == QtCore.Qt.CheckState.Checked)
            self.set_document(doc)
        except (ValueError, IndexError) as exc:
            self.refresh()
            self.statusBar().showMessage(f"参数未应用：{exc}")

    def edit_raw(self, change):
        index, gain = change
        doc = copy.deepcopy(self.session.document)
        doc.raw[index][1] = gain
        self.set_document(doc)

    def edit_peq(self, change):
        index, frequency, gain = change
        doc = copy.deepcopy(self.session.document)
        f = doc.filters[self._peq_map[index]]
        f.frequency = frequency
        f.gain = gain
        self.set_document(doc)

    def undo(self):
        self._preview_document = None
        self.session.undo()
        self.restore_curves()
        self.restore_context_controls()
        self._changed()

    def redo(self):
        self._preview_document = None
        self.session.redo()
        self.restore_curves()
        self.restore_context_controls()
        self._changed()

    def _changed(self):
        self.edit_generation += 1
        self.fit_report = None
        self.session.measurements, self.session.targets = self.measurements, self.targets
        self.session.measurement_index = self.measurement_combo.currentIndex()
        self.session.target_index = self.target_combo.currentIndex()
        self.refresh()
        try:
            self.session.save(self.auto_path)
        except OSError as exc:
            self.statusBar().showMessage(f"自动保存失败：{exc}")

    def refresh(self, *_):
        if not hasattr(self, 'gains_label'):
            return
        doc = self._preview_document or self.session.document
        bank = self.firmware.profiles if self.firmware else None
        writable = bool(bank)
        for action in self.firmware_actions:
            action.setEnabled(writable)
        queued = ', '.join(p['destination']+' ← '+p['eq']['name'] for p in self.session.firmware_plans)
        self.firmware_edits_label.setText('待导出：'+(queued or '无')+f' · 元数据 {len(self.session.metadata_edits)} 项')
        self.firmware_edits_label.setVisible(bool(queued or self.session.metadata_edits))
        if self.firmware:
            self.metadata_text.setPlainText(json.dumps({'original': self.firmware.package['summary'],
                'configuration': bank['configuration'] if bank else None,
                'pending_presets': [{'destination': p['destination'], 'baseline': p.get('baseline'),
                                      'records': len(p['records'])} for p in self.session.firmware_plans],
                'pending_metadata': self.session.metadata_edits}, ensure_ascii=False, indent=2))
        key = self.current_key()
        for name, button in self.preset_buttons.items():
            button.setChecked(name == key)
        special = bool(bank and key and config(bank, key).get('special'))
        self.state_combo.setEnabled(bool(bank) and not special)
        label = '未打开固件 · 可直接编辑 EQ／导入频响'
        if self.firmware:
            label = f'{Path(self.firmware.path).name} · {self.firmware.recognition}'
            self.firmware_label.setToolTip(f'{self.firmware.path}\nSHA-256 {self.firmware.sha256}')
        self.firmware_label.setText(label)
        self.undo_action.setEnabled(bool(self.session._undo))
        self.redo_action.setEnabled(bool(self.session._redo))
        for action, history, title in ((self.undo_action, self.session._undo, '撤销'),
                                        (self.redo_action, self.session._redo, '重做')):
            name = history[-1]['action'] if history else ''
            action.setText(title+('：'+name[:6]+('…' if len(name) > 6 else '') if name else ''))
            action.setToolTip(title+'：'+name if name else title)
        self.eq_label.setText(f'{doc.name} · RAW {len(doc.raw)} 点 · PEQ {len(doc.filters)} 个')
        self.filters_table.blockSignals(True)
        self.filters_table.setRowCount(len(doc.filters))
        for row, f in enumerate(doc.filters):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.CheckState.Checked if f.enabled else QtCore.Qt.CheckState.Unchecked)
            self.filters_table.setItem(row, 0, check)
            for column, value in enumerate((f.id, f.kind, f.frequency, f.gain, f.q), 1):
                self.filters_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.filters_table.blockSignals(False)
        self._peq_map = [i for i, f in enumerate(doc.filters) if f.enabled]
        frequency = np.geomspace(20, 20000, 4096)
        fs = int(self.rate_combo.currentText())
        state = 0 if special else max(0, self.state_combo.currentIndex())
        raw_doc = copy.deepcopy(doc)
        raw_doc.filters = []
        raw_values, eq_values = correction(raw_doc, frequency, fs), correction(doc, frequency, fs)
        self.main_plot.clear_curves()
        difference, difference_error = None, ''
        try:
            difference = self.difference(frequency)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            difference_error = str(exc)
        records = []
        plan = next((p for p in self.session.firmware_plans if p['destination'] == key), None)
        if plan is None:
            plan = next((p for p in reversed(self.session.firmware_plans) if p.get('baseline') == key), None)
        if bank and key:
            for role in sorted(tables_for(bank, self.region_combo.currentText()), key=lambda r: r['output']):
                record = record_at(bank, role['name'], key, state)
                records.append((role, record))
                if self.curve_checks.get('dac'+role['output']) and self.curve_checks['dac'+role['output']].isChecked():
                    color = '#bf8ae8' if role['output'] == '1' else '#e484a4'
                    show_difference = self.dac_mode_combo.currentData() == 'difference'
                    if show_difference and difference:
                        self.main_plot.plot(frequency, difference['branches'][role['output']], color=color,
                            name=f"DAC{role['output']} · 相对参考差分")
                    elif not show_difference:
                        self.main_plot.plot(frequency, firmware_curve(record, frequency, fs), color=color,
                            name=f"DAC{role['output']} · 当前滤波增益")
                    if plan and not show_difference:
                        planned = next(c['record'] for c in plan['records'] if c['table'] == role['name'] and c['state'] == state)
                        self.main_plot.plot(frequency, firmware_response(planned['filters'], frequency, fs), color=color,
                            dashed=True, name=f"DAC{role['output']} · 待导出 {plan['destination']}")
        self.firmware_table.setRowCount(sum(r['count'] for _,r in records))
        row = 0
        gains = []
        for role, record in records:
            gains.append(f"DAC{role['output']}: gain0 {record['gain0']:.4g} / gain1 {record['gain1']:.4g} dB")
            for f in record['slots'][:record['count']]:
                for col, value in enumerate((role['output'], f['type_id'], f['gain'], f['fc'], f['q'])):
                    self.firmware_table.setItem(row, col, QtWidgets.QTableWidgetItem(str(value)))
                row += 1
        self.gains_label.setText('\n'.join(gains) or '固件滤波器和增益字段只读；通过拟合或粘贴生成修改计划。')
        base, spl_offset, bands = np.zeros_like(frequency), 0., []
        measurement_index = self.measurement_combo.currentIndex()
        has_measurement = 0 <= measurement_index < len(self.measurements)
        if has_measurement:
            m = self.measurements[measurement_index]
            x, values = np.asarray(m.frequencies), np.asarray(m.spl_values)
            spl_offset = float(np.interp(np.log(1000), np.log(x), values))
            values = values-spl_offset
            base = np.interp(np.log(frequency), np.log(x), values)
            if self.curve_checks['original'].isChecked():
                self.main_plot.plot(x, values, color='#759ecb', name='原始实测')
            if difference:
                base += difference['delta']
                if self.curve_checks['current'].isChecked():
                    visible = (frequency >= x[0]) & (frequency <= x[-1])
                    self.main_plot.plot(frequency[visible], base[visible], color='#a8c5f0', dashed=True,
                                        name='当前固件估计')
                if difference['uncertain_band']:
                    bands.append({'low': difference['uncertain_band'][0], 'high': difference['uncertain_band'][1],
                                  'label': '双单元交叠 · 模型近似'})
            visible = (frequency >= x[0]) & (frequency <= x[-1])
            if self.curve_checks['estimated'].isChecked():
                self.main_plot.plot(frequency[visible], (base+eq_values)[visible], color='#32cbb9', name='编辑后估计')
            if plan and self.curve_checks['planned'].isChecked():
                try:
                    pending = self.difference(frequency, [plan], plan['destination'])
                    if pending:
                        original = np.interp(np.log(frequency), np.log(x), values)
                        self.main_plot.plot(frequency[visible], (original+pending['delta'])[visible], color='#f38b5d',
                            dashed=True, name=f"待导出估计 · {plan['destination']}")
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    difference_error = str(exc)
        target_index = self.target_combo.currentIndex()
        if 0 <= target_index < len(self.targets) and self.curve_checks['target'].isChecked():
            target = self.targets[target_index]
            target_values = np.asarray(target.spl_values)
            target_values = target_values-np.interp(np.log(1000), np.log(target.frequencies), target_values)
            self.main_plot.plot(target.frequencies, target_values, color='#e3ad55', dashed=True, name='目标频响 · 1kHz 对齐')
        if (self.curve_checks['correction'].isChecked() or not has_measurement) and (doc.raw or doc.filters):
            self.main_plot.plot(frequency, eq_values, color='#e7d6ae', name='修正 EQ · 0dB 基准')
        self.curve_checks['current'].setEnabled(bool(difference and has_measurement))
        self.curve_checks['planned'].setEnabled(bool(plan))
        for output in ('1', '2'):
            self.curve_checks['dac'+output].setEnabled(bool(bank))
        self.main_plot.setLabel('left', 'dB · EQ 增益／相对声压')
        self.main_plot.set_editor(label=f"调音 · {fs/1000:g} kHz"+(f' · {self.region_combo.currentText()}' if bank else ''),
            filters=[asdict(f) for f in doc.filters], raw=doc.raw,
            nodeBase=np.column_stack((frequency, base+raw_values)).tolist(),
            rawBase=np.column_stack((frequency, base)).tolist(),
            addBase=np.column_stack((frequency, base+eq_values)).tolist(), bands=bands,
            splOffset=spl_offset if has_measurement and not self.relative_check.isChecked() else None,
            empty='选择原始测量，或双击添加 PEQ。固件两路滤波链可用复选框叠加。')
        if difference and has_measurement:
            assumption = '参考关系为假定；' if self.reference_assumed.isChecked() else ''
            self.quality_label.setText(assumption+difference['mode']+'。编辑后估计 = 当前固件估计 + 修正；虚线橙色来自待写入参数。')
        elif difference_error:
            self.quality_label.setText('固件差分未计算：'+difference_error+'。编辑后曲线暂为实测＋修正。')
        else:
            self.quality_label.setText('编辑后估计 = 实测＋修正。绑定参考固件后加入当前固件差分；所有曲线共享频率轴和 dB 比例。')

    def closeEvent(self, event):
        if self.workers or self.network_workers:
            self.statusBar().showMessage("正在读取文件，完成后即可关闭窗口。")
            event.ignore()
        else:
            event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'selection_area'):
            # Keep the graph usable on short screens; selections remain accessible by scrolling.
            self.selection_area.setMaximumHeight(max(130, self.height()-610))
