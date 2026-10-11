"""Local Qt studio: workflow rail, one full-height response graph and validated firmware plans.

Layout: the left rail follows the workflow (measurement → target → correction → firmware
preset → export); the embedded graph takes all remaining space; tables and firmware JSON
live in dockable panels. All computation stays in the Qt-independent core modules.
"""

import copy
import json
import threading
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from heytap_eq import dialogs, metadata
from heytap_eq import theme as themes
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
from heytap_eq.firmware_dsp import FILTER_TYPES
from heytap_eq.firmware_dsp import response as firmware_response
from heytap_eq.firmware_edit import export_firmware, make_plan
from heytap_eq.fitting import fit_response
from heytap_eq.flowmix import FlowmixClient
from heytap_eq.measurements import load_measurements
from heytap_eq.preferences import BrowserMemory, device_identity
from heytap_eq.service_profile import builtin_authorization
from heytap_eq.session import Session, atomic_json
from heytap_eq.web_plot import ResponsePlot
from heytap_eq.widgets import StepCard, apply_palette, button, label, mark, row, write_icons

CURVES = (("original", "原始实测", True), ("current", "当前固件估计", True), ("estimated", "编辑后估计", True),
          ("target", "目标频响", True), ("planned", "待导出估计", True), ("correction", "修正 EQ", False),
          ("dac1", "DAC1（输出1）", False), ("dac2", "DAC2（输出2）", False))
DAC_MODES = (("chain", "滤波链"), ("difference", "相对参考差分"))
COMMANDS = ("import_measurements", "import_target", "import_eq", "open_firmware")
FIRMWARE_KINDS = {0: "LS", 1: "PEAK", 2: "HS", 3: "LP", 4: "HP", 5: "AP"}


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
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1440, int(screen.width()*.94)), min(900, int(screen.height()*.92)))
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
        self._metadata_json = None
        self._plans_shown = None
        self._restoring_online = False
        self._online_manual = False
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
        self.appearance_path = self.auto_path.parent/"appearance.json"
        self.appearance = themes.load(self.appearance_path)
        self.theme = themes.resolve(self.appearance)
        self._setup()
        self.apply_theme()
        QtGui.QGuiApplication.styleHints().colorSchemeChanged.connect(self.system_scheme_changed)
        if recover and self.auto_path.exists():
            try:
                self.session.restore(self.auto_path)
                self.statusBar().showMessage("已恢复上次 EQ 工程；重新打开原固件后会核对 SHA-256。")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.statusBar().showMessage(f"恢复文件未加载：{exc}")
        self.restore_curves()
        self.restore_context_controls()
        if self.measurements or self.targets:
            self.main_plot.request_fit()
        self.refresh()

    # ------------------------------------------------------------------ layout
    def _action(self, menu, label_text, fn, shortcut=None, checkable=False, tip=None):
        action = QtGui.QAction(label_text, self)
        action.setCheckable(checkable)
        (action.toggled if checkable else action.triggered).connect(fn)
        if shortcut:
            action.setShortcut(shortcut)
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        menu.addAction(action)
        return action

    def _setup(self):
        self.setDockNestingEnabled(True)
        self._build_menus()
        self._build_toolbar()
        self.rail = self._build_rail()
        self.main_plot = ResponsePlot("dB · 相对频响／EQ 增益")
        # Compatibility aliases refer to the same view, never two independent graphs.
        self.digital_plot = self.acoustic_plot = self.main_plot
        self.split = QtWidgets.QSplitter()
        self.split.addWidget(self.rail)
        self.split.addWidget(self.main_plot)
        self.split.setStretchFactor(1, 1)
        self.split.setCollapsible(1, False)
        self.split.setSizes([330, max(400, self.width()-330)])
        self.setCentralWidget(self.split)
        self._build_docks()
        self._build_status()
        self.rail_action.toggled.connect(self.rail.setVisible)
        self.main_plot.editRequested.connect(self.editor_event)
        self.main_plot.error.connect(lambda message: self.statusBar().showMessage(message))
        self._peq_map = []
        self.reset_plots()
        self.statusBar().showMessage("按左侧步骤：原始频响 → 目标 → 修正 EQ → 固件预设 → 导出。")

    def _build_menus(self):
        bar = self.menuBar()
        files = bar.addMenu("工程")
        self._action(files, "新建工程", self.new_project, "Ctrl+N")
        self._action(files, "打开工程…", self.open_project)
        self._action(files, "保存工程…", self.save_project, "Ctrl+S")
        files.addSeparator()
        self._action(files, "导入 EQ…", self.import_eq, "Ctrl+I")
        self._action(files, "导出 EQ 文本…", self.export_eq)
        files.addSeparator()
        self._action(files, "导入原始测量…", self.import_measurements)
        self._action(files, "导入目标频响…", self.import_target)
        edit = bar.addMenu("编辑")
        self.undo_action = self._action(edit, "撤销", self.undo, "Ctrl+Z")
        self.redo_action = self._action(edit, "重做", self.redo, QtGui.QKeySequence("Ctrl+Shift+Z"))
        edit.addSeparator()
        self.copy_action = self._action(edit, "复制调音", self.copy_tuning, "Ctrl+Shift+C",
                                        tip="复制 RAW／PEQ、启用状态与测量、目标、参考关联")
        self.copy_preset_action = self._action(edit, "复制完整固件预设", self.copy_preset)
        self.paste_action = self._action(edit, "粘贴调音", self.paste_tuning, "Ctrl+Shift+V",
                                         tip="支持软件 JSON、Flowmix／GraphicEQ／Equalizer APO 文本和完整固件预设")
        firmware = bar.addMenu("固件")
        self.open_firmware_action = self._action(firmware, "打开固件…", self.open_firmware, "Ctrl+O")
        firmware.addSeparator()
        self.fit_action = self._action(firmware, "拟合到固件预设…", self.start_firmware_fit)
        self.metadata_action = self._action(firmware, "编辑固件信息…", self.edit_metadata)
        self.export_action = self._action(firmware, "导出固件…", self.start_firmware_export, "Ctrl+E")
        self.clear_action = self._action(firmware, "清空固件修改", self.clear_firmware_edits)
        firmware.addSeparator()
        self.mapping_action = self._action(firmware, "载入配置映射…", self.import_mapping)
        self.scan_action = self._action(firmware, "候选结构扫描", self.scan_candidates)
        self.firmware_actions = [self.fit_action, self.metadata_action, self.export_action, self.clear_action]
        self.firmware_menu = firmware
        view = bar.addMenu("视图")
        curves = view.addMenu("曲线")
        self.curve_checks = {}
        for key, title, checked in CURVES:
            action = QtGui.QAction(title, self)
            action.setCheckable(True)
            action.setChecked(checked)
            action.toggled.connect(self.visibility_changed)
            curves.addAction(action)
            self.curve_checks[key] = action
        curves.addSeparator()
        self.relative_check = QtGui.QAction("相对声压（隐藏 SPL 刻度）", self)
        self.relative_check.setCheckable(True)
        self.relative_check.setChecked(True)
        self.relative_check.toggled.connect(self.visibility_changed)
        curves.addAction(self.relative_check)
        dac = view.addMenu("DAC 曲线")
        self.dac_mode_group = QtGui.QActionGroup(self)
        self.dac_mode_actions = {}
        for key, title in DAC_MODES:
            action = QtGui.QAction(title, self.dac_mode_group)
            action.setCheckable(True)
            action.setChecked(key == "chain")
            action.toggled.connect(self.visibility_changed)
            dac.addAction(action)
            self.dac_mode_actions[key] = action
        view.addSeparator()
        self.rail_action = self._action(view, "工作流侧栏", lambda *_: None, "Ctrl+B", checkable=True)
        self.rail_action.setChecked(True)
        self.view_menu = view
        view.addSeparator()
        self.appearance_menu = view.addMenu("外观")
        self.mode_group = QtGui.QActionGroup(self)
        self.mode_actions = {}
        for key, title in themes.MODES.items():
            action = QtGui.QAction(title, self.mode_group)
            action.setCheckable(True)
            action.triggered.connect(lambda _=False, key=key: self.set_appearance(mode=key))
            self.appearance_menu.addAction(action)
            self.mode_actions[key] = action
        self.appearance_menu.addSeparator()
        self.accent_group = QtGui.QActionGroup(self)
        self.accent_actions = {}
        for key, (title, colors) in themes.ACCENTS.items():
            action = QtGui.QAction(f"强调色 · {title}", self.accent_group)
            action.setCheckable(True)
            action.triggered.connect(lambda _=False, key=key: self.set_appearance(accent=key))
            self.appearance_menu.addAction(action)
            self.accent_actions[key] = action

    def _build_toolbar(self):
        toolbar = self.addToolBar("常用")
        toolbar.setObjectName("toolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        for action in (self.open_firmware_action,):
            toolbar.addAction(action)
        self.import_eq_action = QtGui.QAction("导入 EQ", self)
        self.import_eq_action.triggered.connect(self.import_eq)
        toolbar.addAction(self.import_eq_action)
        toolbar.addSeparator()
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()
        copy_button = QtWidgets.QToolButton()
        copy_button.setDefaultAction(self.copy_action)
        copy_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        copy_menu = QtWidgets.QMenu(copy_button)
        copy_menu.addAction(self.copy_preset_action)
        copy_button.setMenu(copy_menu)
        toolbar.addWidget(copy_button)
        toolbar.addAction(self.paste_action)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.firmware_label = label("未打开固件", "chip", wrap=False)
        self.firmware_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar.addWidget(self.firmware_label)
        appearance = QtWidgets.QToolButton()
        appearance.setText("外观")
        appearance.setToolTip("主题：跟随系统／浅色／深色，以及强调色")
        appearance.setMenu(self.appearance_menu)
        appearance.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(appearance)

    def _build_rail(self):
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("rail")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(270)
        scroll.setMaximumWidth(460)
        body = QtWidgets.QWidget()
        body.setObjectName("railBody")
        scroll.setWidget(body)
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 6, 8)
        layout.setSpacing(8)
        self.cards = {}

        card = self.cards["measurement"] = StepCard(1, "原始频响")
        self.measurement_combo = QtWidgets.QComboBox()
        self.measurement_combo.setPlaceholderText("尚未载入测量")
        self.measurement_combo.currentIndexChanged.connect(self.curve_selection_changed)
        card.add(self.measurement_combo)
        card.add(row(button("导入文件…", self.import_measurements, tooltip="CSV／TXT／FRD／JSON／ReaLab HAR"),
                     button("设为目标", self.measurement_as_target, tooltip="把当前测量设为目标（例如另一副耳机）")))
        self.online_toggle = button("在线测量", checkable=True, flat=True,
                                    tooltip="从在线测量库按来源、品牌、型号选择")
        self.online_toggle.setStyleSheet("text-align: left;")
        self.online_toggle.toggled.connect(self.online_toggled)
        card.add(self.online_toggle)
        self.online_block = QtWidgets.QWidget()
        online = QtWidgets.QVBoxLayout(self.online_block)
        online.setContentsMargins(0, 0, 0, 0)
        online.setSpacing(6)
        self.source_combo, self.brand_combo, self.headphone_combo = [QtWidgets.QComboBox() for _ in range(3)]
        for combo, placeholder in ((self.source_combo, "数据源"), (self.brand_combo, "搜索品牌"),
                                   (self.headphone_combo, "搜索型号")):
            combo.setEditable(True)
            combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
            combo.lineEdit().setPlaceholderText(placeholder)
            combo.completer().setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
            combo.completer().setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
            online.addWidget(combo)
        self.refresh_online = button("刷新在线索引", self.connect_flowmix, flat=True)
        online.addWidget(self.refresh_online)
        card.add(self.online_block)
        self.source_combo.activated.connect(self.source_chosen)
        self.brand_combo.activated.connect(self.brand_chosen)
        self.headphone_combo.activated.connect(self.load_online_measurements)
        self.online_controls = [self.source_combo, self.brand_combo, self.headphone_combo, self.refresh_online]
        self.reference_block = QtWidgets.QWidget()
        reference = QtWidgets.QVBoxLayout(self.reference_block)
        reference.setContentsMargins(0, 0, 0, 0)
        reference.setSpacing(6)
        reference.addWidget(label("测量对应的固件", "section"))
        self.reference_label = label("未绑定参考固件", "muted")
        reference.addWidget(self.reference_label)
        self.reference_preset_combo = QtWidgets.QComboBox()
        self.reference_preset_combo.setToolTip("测量时耳机使用的预设")
        self.reference_preset_combo.currentIndexChanged.connect(self.context_changed)
        reference.addWidget(row(self.reference_preset_combo, button("选择…", self.import_reference,
                                                                    tooltip="选择测量所对应的原固件")))
        self.reference_assumed = QtWidgets.QCheckBox("参考关系为假定")
        self.reference_assumed.setToolTip("测量来源没有声明准确的固件版本与预设时保持勾选")
        self.reference_assumed.setChecked(True)
        self.reference_assumed.toggled.connect(self.context_changed)
        reference.addWidget(self.reference_assumed)
        card.add(self.reference_block)
        layout.addWidget(card)

        card = self.cards["target"] = StepCard(2, "目标频响")
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.setPlaceholderText("尚未选择目标")
        self.target_combo.currentIndexChanged.connect(self.curve_selection_changed)
        card.add(self.target_combo)
        self.library_combo = QtWidgets.QComboBox()
        self.library_combo.setEditable(True)
        self.library_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.library_combo.lineEdit().setPlaceholderText("搜索在线目标曲线库")
        self.library_combo.completer().setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        self.library_combo.completer().setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.library_combo.activated.connect(self.target_library_chosen)
        card.add(self.library_combo)
        card.add(row(button("导入文件…", self.import_target, tooltip="AutoEQ CSV、REW、squig.link 文本等")))
        layout.addWidget(card)

        card = self.cards["correction"] = StepCard(3, "修正 EQ")
        self.quality_label = label("", "muted")
        self.quality_label.hide()
        card.add(self.quality_label)
        self.fit_target_button = button("生成修正 EQ…", self.start_fit, primary=True,
                                        tooltip="根据原始与目标频响生成 RAW 或 PEQ 修正")
        card.add(self.fit_target_button)
        card.add(row(button("导入…", self.import_eq), button("导出…", self.export_eq)))
        self.side_button = button("参数表", checkable=True, tooltip="显示 PEQ 与当前固件滤波器表格")
        card.add(self.side_button)
        layout.addWidget(card)

        card = self.cards["firmware"] = StepCard(4, "固件预设")
        self.firmware_hint = label("打开已识别的固件后，选择基底预设并把修正拟合进去。没有固件也能完成 1–3 步。", "muted")
        card.add(self.firmware_hint)
        self.firmware_open_button = button("打开固件…", self.open_firmware)
        card.add(self.firmware_open_button)
        self.firmware_body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(self.firmware_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        body_layout.addWidget(label("调音基底", "section"))
        self.preset_group = QtWidgets.QButtonGroup(self)
        self.preset_group.setExclusive(True)
        self.preset_buttons = {}
        self.preset_grid = QtWidgets.QGridLayout()
        self.preset_grid.setSpacing(6)
        body_layout.addLayout(self.preset_grid)
        # Kept as the single source of the selected configuration key.
        self.preset_combo = QtWidgets.QComboBox(self)
        self.preset_combo.hide()
        self.preset_combo.currentIndexChanged.connect(self.context_changed)
        self.advanced_button = button("计算详情", checkable=True, flat=True,
                                      tooltip="区域、内部状态、采样率与声学模型假设")
        body_layout.addWidget(self.advanced_button)
        self.advanced_controls = QtWidgets.QWidget()
        details = QtWidgets.QFormLayout(self.advanced_controls)
        details.setContentsMargins(0, 0, 0, 0)
        details.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.region_combo = QtWidgets.QComboBox()
        self.state_combo = QtWidgets.QComboBox()
        self.rate_combo = QtWidgets.QComboBox()
        self.rate_combo.addItems(["44100", "48000", "96000"])
        self.rate_combo.setCurrentText("48000")
        self.crossover_spin = QtWidgets.QDoubleSpinBox()
        self.crossover_spin.setRange(1000, 20000)
        self.crossover_spin.setValue(15000)
        self.crossover_spin.setSuffix(" Hz")
        self.crossover_spin.setDecimals(0)
        self.gain_mode_combo = QtWidgets.QComboBox()
        for title, key in (("仅滤波器差分", "none"), ("假定整体增益按 gain0", "gain0"),
                           ("假定整体增益按 gain1", "gain1")):
            self.gain_mode_combo.addItem(title, key)
        self.reference_state_combo = QtWidgets.QComboBox()
        for title, widget in (("区域", self.region_combo), ("内部状态", self.state_combo),
                              ("计算采样率", self.rate_combo), ("双单元交接", self.crossover_spin),
                              ("增益模型", self.gain_mode_combo), ("测量参考状态", self.reference_state_combo)):
            details.addRow(title, widget)
        for widget in (self.region_combo, self.state_combo, self.rate_combo, self.gain_mode_combo,
                       self.reference_state_combo):
            widget.currentIndexChanged.connect(self.context_changed)
        self.crossover_spin.valueChanged.connect(self.context_changed)
        self.advanced_controls.hide()
        self.advanced_button.toggled.connect(self.advanced_controls.setVisible)
        body_layout.addWidget(self.advanced_controls)
        self.firmware_fit_button = button("拟合到固件预设…", self.start_firmware_fit, primary=True)
        body_layout.addWidget(self.firmware_fit_button)
        body_layout.addWidget(label("修改计划", "section"))
        self.plans_list = QtWidgets.QListWidget()
        self.plans_list.setMaximumHeight(96)
        self.plans_list.setToolTip("导出时一并写入；每一项都可以撤销")
        body_layout.addWidget(self.plans_list)
        self.remove_plan_button = button("移除所选", self.remove_selected_plan, flat=True)
        body_layout.addWidget(self.remove_plan_button)
        card.add(self.firmware_body)
        layout.addWidget(card)

        card = self.cards["export"] = StepCard(5, "导出")
        self.firmware_edits_label = label("", "muted")
        card.add(self.firmware_edits_label)
        card.add(row(button("编辑固件信息…", self.edit_metadata), button("清空修改", self.clear_firmware_edits)))
        self.export_button = button("导出固件…", self.start_firmware_export, primary=True)
        card.add(self.export_button)
        layout.addWidget(card)
        layout.addStretch()
        return scroll

    def _build_docks(self):
        self.side = QtWidgets.QWidget()
        side = QtWidgets.QVBoxLayout(self.side)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(6)
        self.eq_label = label("", "muted")
        side.addWidget(self.eq_label)
        self.filters_table = QtWidgets.QTableWidget(0, 6)
        self.filters_table.setHorizontalHeaderLabels(["启用", "ID", "类型", "频率 Hz", "增益 dB", "Q"])
        self.filters_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.filters_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.filters_table.verticalHeader().hide()
        self.filters_table.setAlternatingRowColors(True)
        self.filters_table.itemChanged.connect(self.edit_filter)
        side.addWidget(self.filters_table, 3)
        side.addWidget(row(button("添加 PEQ", self.add_filter), button("删除所选", self.remove_filter)))
        side.addWidget(label("当前固件记录（只读）", "section"))
        self.firmware_table = QtWidgets.QTableWidget(0, 5)
        self.firmware_table.setHorizontalHeaderLabels(["输出", "类型", "增益 dB", "频率 Hz", "Q"])
        self.firmware_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.firmware_table.verticalHeader().hide()
        self.firmware_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.firmware_table.setAlternatingRowColors(True)
        side.addWidget(self.firmware_table, 2)
        self.gains_label = label("", "muted")
        side.addWidget(self.gains_label)
        self.params_dock = self._dock("参数表", self.side, "paramsDock")
        self.metadata_text = QtWidgets.QPlainTextEdit()
        self.metadata_text.setReadOnly(True)
        self.info_dock = self._dock("固件信息", self.metadata_text, "infoDock")
        self.tabifyDockWidget(self.params_dock, self.info_dock)
        self.params_dock.raise_()
        for dock in (self.params_dock, self.info_dock):
            dock.hide()
            self.view_menu.insertAction(self.rail_action, dock.toggleViewAction())
        self.side_button.toggled.connect(self.params_dock.setVisible)
        self.params_dock.visibilityChanged.connect(self.side_button.setChecked)
        self.firmware_menu.addAction(self.info_dock.toggleViewAction())

    def _dock(self, title, widget, name):
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable |
                         QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable)
        dock.setMinimumWidth(320)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        return dock

    def _build_status(self):
        status = self.statusBar()
        self.busy = QtWidgets.QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(110)
        self.busy.setTextVisible(False)
        self.busy.hide()
        self.cancel_button = button("取消", self.cancel_fit, flat=True, tooltip="取消当前拟合或封包")
        self.cancel_button.hide()
        status.addPermanentWidget(self.busy)
        status.addPermanentWidget(self.cancel_button)

    def online_toggled(self, expanded):
        self.online_block.setVisible(expanded)
        self.online_toggle.setText(("▾ " if expanded else "▸ ")+"在线测量")
        if not self._restoring_online:
            self._online_manual = True

    def _update_busy(self):
        self.busy.setVisible(bool(self.workers or self.network_workers))
        self.cancel_button.setVisible(bool(self.workers))

    # ------------------------------------------------------------------ appearance
    def system_is_dark(self):
        scheme = QtGui.QGuiApplication.styleHints().colorScheme()
        return scheme != QtCore.Qt.ColorScheme.Light

    def apply_theme(self):
        self.theme = themes.resolve(self.appearance, self.system_is_dark())
        apply_palette(self.theme)
        try:
            icons = write_icons(self.theme, self.auto_path.parent/"theme-icons")
        except OSError:
            icons = None  # Native arrows remain usable if the settings folder is read-only.
        self.setStyleSheet(themes.stylesheet(self.theme, icons))
        self.mode_actions[self.appearance["mode"]].setChecked(True)
        self.accent_actions[self.appearance["accent"]].setChecked(True)
        self.main_plot.set_theme(themes.web(self.theme))
        self.refresh()

    def set_appearance(self, mode=None, accent=None):
        self.appearance = themes.validate({"mode": mode or self.appearance["mode"],
                                           "accent": accent or self.appearance["accent"]})
        try:
            atomic_json(self.appearance_path, self.appearance)
        except OSError as exc:
            self.statusBar().showMessage(f"外观设置未保存：{exc}")
        self.apply_theme()

    def system_scheme_changed(self, *_):
        if self.appearance["mode"] == "system":
            self.apply_theme()

    # ------------------------------------------------------------------ context
    def current_key(self):
        return self.preset_combo.currentData()

    def configure_firmware(self):
        bank = self.firmware.profiles if self.firmware else None
        for button_ in self.preset_buttons.values():
            self.preset_group.removeButton(button_)
            button_.deleteLater()
        while self.preset_grid.count():
            item = self.preset_grid.takeAt(0)
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
            ordinary = [p for p in configurations(bank) if not p.get("special")]
            special = [p for p in configurations(bank) if p.get("special")]
            for index, preset in enumerate(ordinary+special):
                self.preset_combo.addItem(preset["title"], preset["key"])
                preset_button = button(preset["title"], checkable=True)
                if preset.get("special"):
                    preset_button.setToolTip("适配器定义的特殊配置；只与特殊配置互相拟合或复制")
                preset_button.clicked.connect(lambda checked=False, key=preset["key"]:
                                              self.preset_combo.setCurrentIndex(self.preset_combo.findData(key)))
                self.preset_group.addButton(preset_button)
                self.preset_buttons[preset["key"]] = preset_button
            for index, preset in enumerate(ordinary):
                self.preset_grid.addWidget(self.preset_buttons[preset["key"]], index//2, index % 2)
            if special:
                base = (len(ordinary)+1)//2
                self.preset_grid.addWidget(label("适配器特殊配置", "section"), base, 0, 1, 2)
                for index, preset in enumerate(special):
                    self.preset_grid.addWidget(self.preset_buttons[preset["key"]], base+1+index//2, index % 2)
            self.region_combo.addItems(regions(bank))
            self.state_combo.addItems(["内部状态 "+str(s) for s in bank["configuration"]["states"]])
        for combo in (self.preset_combo, self.region_combo, self.state_combo):
            combo.blockSignals(False)
        self.restore_context_controls()

    def restore_context_controls(self):
        self._restoring = True
        context = self.session.context
        index = self.preset_combo.findData(context.get("preset"))
        self.preset_combo.setCurrentIndex(index if index >= 0 else (0 if self.preset_combo.count() else -1))
        self.region_combo.setCurrentText(context.get("region", "other"))
        self.state_combo.setCurrentIndex(min(int(context.get("state", 0)), max(0, self.state_combo.count()-1)))
        self.rate_combo.setCurrentText(str(context.get("rate", 48000)))
        self.crossover_spin.setValue(float(context.get("crossover", 15000)))
        self.gain_mode_combo.setCurrentIndex(max(0, self.gain_mode_combo.findData(context.get("gain_mode", "none"))))
        self.reference_assumed.setChecked(context.get("reference_assumed", True))
        reference = context.get("reference")
        self.reference_preset_combo.clear()
        self.reference_state_combo.clear()
        if reference:
            self.reference_label.setText(reference.get("title", "自定义参考"))
            for preset in reference.get("presets", []):
                self.reference_preset_combo.addItem(preset["title"], preset["key"])
            self.reference_preset_combo.setCurrentIndex(max(0, self.reference_preset_combo.findData(
                context.get("reference_preset", "丹拿原声"))))
            ordinary = next((p for p in reference["presets"] if not p.get("special")), None)
            self.reference_state_combo.addItems([str(s) for s in ordinary.get("states", [])] if ordinary else ["特殊状态"])
            self.reference_state_combo.setCurrentIndex(min(int(context.get("reference_state", 0)),
                                                           max(0, self.reference_state_combo.count()-1)))
        else:
            self.reference_label.setText("未绑定参考固件")
        for key, check in self.curve_checks.items():
            check.setChecked(self.session.view_options.get(key, check.isChecked()))
        self.relative_check.setChecked(self.session.view_options.get("relative", True))
        mode = self.session.view_options.get("dac_mode", "chain")
        self.dac_mode_actions.get(mode, self.dac_mode_actions["chain"]).setChecked(True)
        self._restoring = False

    def context_changed(self, *_):
        if self._restoring or not hasattr(self, "main_plot"):
            return
        special = bool(self.firmware and self.firmware.profiles and self.current_key()
                       and config(self.firmware.profiles, self.current_key()).get("special"))
        changes = {"preset": self.current_key(), "region": self.region_combo.currentText() or "other",
                   "state": 0 if special else max(0, self.state_combo.currentIndex()),
                   "rate": int(self.rate_combo.currentText()),
                   "reference_preset": self.reference_preset_combo.currentData(),
                   "reference_assumed": self.reference_assumed.isChecked(), "crossover": self.crossover_spin.value(),
                   "gain_mode": self.gain_mode_combo.currentData(),
                   "reference_state": max(0, self.reference_state_combo.currentIndex())}
        index = self.measurement_combo.currentIndex()
        if 0 <= index < len(self.measurements) and self.session.context.get("reference"):
            bindings = copy.deepcopy(self.session.context.get("reference_bindings", {}))
            bindings[measurement_key(self.measurements[index])] = {
                **{k: self.session.context[k] for k in ("reference",) if k in self.session.context},
                **{k: changes[k] for k in ("reference_preset", "reference_state", "reference_assumed")}}
            changes["reference_bindings"] = bindings
        self.session.set_context(changes)
        self._changed()

    def dac_mode(self):
        return next((key for key, action in self.dac_mode_actions.items() if action.isChecked()), "chain")

    def visibility_changed(self, *_):
        if self._restoring or not hasattr(self, "main_plot"):
            return
        options = {key: c.isChecked() for key, c in self.curve_checks.items()}
        options["relative"] = self.relative_check.isChecked()
        options["dac_mode"] = self.dac_mode()
        if "raw_nodes" in self.session.view_options:
            options["raw_nodes"] = self.session.view_options["raw_nodes"]
        self.session.view_options = options
        self.refresh()
        try:
            self.session.save(self.auto_path)
        except OSError as exc:
            self.statusBar().showMessage(f"显示设置未保存：{exc}")

    def reset_plots(self, *_):
        if not hasattr(self, "main_plot"):
            return
        self.main_plot.reset_range(-24, 24, 5)
        self.refresh()

    # ------------------------------------------------------------------ clipboard
    def copy_tuning(self):
        payload = tuning_payload(self.session)
        self._set_clipboard(payload)
        self.statusBar().showMessage("已复制完整调音，包含 RAW／PEQ 与测量、目标、参考关联。")

    def _set_clipboard(self, payload):
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        data = QtCore.QMimeData()
        data.setData(MIME, text.encode("utf-8"))
        data.setText(text)
        QtWidgets.QApplication.clipboard().setMimeData(data)

    def copy_preset(self):
        if not self.firmware or not self.firmware.profiles or not self.current_key():
            self.statusBar().showMessage("先打开固件并选择要复制的配置。")
            return
        payload = preset_payload(self.firmware, self.current_key(), self.session.firmware_plans)
        self._set_clipboard(payload)
        self.statusBar().showMessage(f"已复制 {payload['title']} 的完整配置：{len(payload['records'])} 条记录。")

    def paste_tuning(self):
        try:
            mime = QtWidgets.QApplication.clipboard().mimeData()
            text = bytes(mime.data(MIME)).decode("utf-8") if mime.hasFormat(MIME) else mime.text()
            payload = decode_text(text)
            if payload["schema"] == PRESET_SCHEMA:
                if not self.firmware or not self.firmware.profiles:
                    raise ValueError("打开目标固件后才能粘贴完整预设；通用 EQ 文本可直接粘贴。")
                presets = [p for p in configurations(self.firmware.profiles)
                           if bool(p.get("special")) == bool(payload.get("special"))]
                labels = [p["title"] for p in presets]
                initial = next((i for i, p in enumerate(presets) if p["key"] == self.current_key()), 0)
                chosen, ok = QtWidgets.QInputDialog.getItem(self, "粘贴完整预设", "写入目标配置", labels, initial, False)
                if not ok:
                    return
                destination = presets[labels.index(chosen)]["key"]
                self.session.stage_plan(copy_plan(self.firmware, payload, destination))
                self.preset_combo.blockSignals(True)
                self.preset_combo.setCurrentIndex(self.preset_combo.findData(destination))
                self.preset_combo.blockSignals(False)
            else:
                self.session.paste_tuning(payload["state"])
                self.restore_curves()
                self.restore_context_controls()
            self._preview_document = None
            self._changed()
            self.statusBar().showMessage("已粘贴；本次操作可一步撤销。")
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            self.statusBar().showMessage(f"粘贴未完成：{exc}")

    # ------------------------------------------------------------------ firmware reference
    def import_mapping(self):
        if not self.firmware:
            self.statusBar().showMessage("先打开固件，再载入它的配置映射。")
            return
        path = self._choose("载入配置映射", "配置映射 (*.json)")
        if path:
            firmware_path = self.firmware.path
            self._task(lambda: inspect_firmware(firmware_path, json.loads(Path(path).read_text(encoding="utf-8"))),
                       self.set_firmware)

    def import_reference(self):
        path = self._choose("选择测量所对应的参考固件")
        if path:
            def received(firmware):
                if not firmware.profiles:
                    raise ValueError("参考固件尚未识别 EQ 映射")
                changes = {"reference": reference_snapshot(firmware), "reference_assumed": True,
                           "reference_preset": configurations(firmware.profiles)[0]["key"], "reference_state": 0}
                index = self.measurement_combo.currentIndex()
                if 0 <= index < len(self.measurements):
                    bindings = copy.deepcopy(self.session.context.get("reference_bindings", {}))
                    bindings[measurement_key(self.measurements[index])] = copy.deepcopy(changes)
                    changes["reference_bindings"] = bindings
                self.session.set_context(changes, "绑定参考固件")
                self.restore_context_controls()
                self._changed()
            mapping = copy.deepcopy(self.session.context.get("mapping"))
            self._task(lambda: inspect_firmware(path, mapping), received)

    def difference(self, frequency, plans=(), current_key=None):
        context = self.session.context
        if not (self.firmware and self.firmware.profiles and context.get("reference") and self.current_key()):
            return None
        return estimate_difference(self.firmware, context["reference"], current_key or self.current_key(),
            self.reference_preset_combo.currentData(), frequency, self.region_combo.currentText(),
            0 if config(self.firmware.profiles, current_key or self.current_key()).get("special")
            else max(0, self.state_combo.currentIndex()),
            int(self.rate_combo.currentText()), self.crossover_spin.value(), self.gain_mode_combo.currentData(), plans,
            max(0, self.reference_state_combo.currentIndex()))

    # ------------------------------------------------------------------ tasks
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
        self._update_busy()
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
        self._update_busy()
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

    # ------------------------------------------------------------------ online measurements
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

    # ------------------------------------------------------------------ curves
    def import_target(self):
        path = self._choose("导入目标频响", "测量 (*.csv *.txt *.frd *.json *.har);;所有文件 (*)")
        if path:
            self._task(lambda: load_measurements(path), self.set_targets)

    def measurement_as_target(self):
        index = self.measurement_combo.currentIndex()
        if index >= 0:
            self.set_targets([copy.deepcopy(self.measurements[index])])
        else:
            self.statusBar().showMessage("先选择一条原始测量。")

    def set_targets(self, curves):
        self.targets = list(self.targets)
        for curve in curves:
            if curve not in self.targets:
                self.targets.append(curve)
        self.fill_curves(self.target_combo, self.targets,
                         self.targets.index(curves[0]) if curves else -1)
        self.curve_selection_changed()

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
        binding = self.session.context.get("reference_bindings", {}).get(measurement_key(self.measurements[index])) \
            if 0 <= index < len(self.measurements) and index != self.session.measurement_index else None
        self.session.set_curves(self.measurements, self.targets, index, self.target_combo.currentIndex(), binding)
        if binding:
            self.restore_context_controls()
        # A different measurement or target usually needs a different dB window.
        self.main_plot.request_fit()
        self._changed()

    # ------------------------------------------------------------------ firmware fit and export
    def cancel_fit(self):
        self.fit_cancel.set()
        self.statusBar().showMessage("正在取消当前拟合或封包任务…")

    def start_firmware_fit(self):
        if not self.firmware or not self.firmware.profiles or self.workers:
            return
        if not self.session.document.raw and not self.session.document.filters:
            self.statusBar().showMessage("先导入、编辑或生成修正 EQ。")
            return
        choice = dialogs.firmware_fit(self, self.firmware.profiles, self.current_key(),
                                      self.session.context.get("preamp_db", 0.))
        if choice:
            baseline, destination, preamp = choice
            self.fit_firmware_preset(destination, preamp, baseline)

    def fit_firmware_preset(self, destination, preamp=0, baseline=None):
        if not self.firmware or not self.firmware.profiles or self.workers:
            return
        firmware, doc = self.firmware, copy.deepcopy(self.session.document)
        baseline = baseline or self.current_key()
        self.session.set_context({"preamp_db": preamp}, "调整整体衰减")
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

    def remove_selected_plan(self):
        item = self.plans_list.currentItem()
        destination = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        if destination is None:
            self.statusBar().showMessage("先在修改计划中选择一项。")
            return
        self.session._checkpoint("移除固件配置："+destination)
        self.session.firmware_plans = [p for p in self.session.firmware_plans if p["destination"] != destination]
        self._changed()

    def edit_metadata(self):
        if not self.firmware or not self.firmware.profiles:
            return
        edits = dialogs.metadata_edits(self, metadata.fields(self.firmware.package), self.session.metadata_edits)
        if edits is None:
            return
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
        limits = dialogs.export_settings(self, self.session.firmware_plans, self.session.metadata_edits,
                                         metadata.fields(self.firmware.package))
        if not limits:
            return
        original = Path(self.firmware.path)
        output = QtWidgets.QFileDialog.getSaveFileName(self, "导出修改后的固件",
                  str(original.with_name(original.stem+"-edited.bin")), "固件 (*.bin);;所有文件 (*)")[0]
        if output:
            self.export_to_path(output, *limits)

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

    # ------------------------------------------------------------------ correction
    def start_fit(self):
        a, b = self.measurement_combo.currentIndex(), self.target_combo.currentIndex()
        if a < 0 or b < 0:
            self.statusBar().showMessage("请先在 ①、② 选择原始频响与目标频响。")
            return
        choice = dialogs.correction_options(self)
        if choice:
            mode, options = choice
            self.fit_measurements(self.measurements[a], self.targets[b], mode, options)

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
            self.refresh()
        self._task(lambda: fit_response(original, target, mode, options, self.fit_cancel), received)

    # ------------------------------------------------------------------ files
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
        self._metadata_json = None
        self.info_dock.show()
        self.info_dock.raise_()
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
        path = self._choose("导入人工耳测量", "测量 (*.csv *.txt *.frd *.json *.har);;所有文件 (*)")
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
        self._metadata_json = None
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
                self.main_plot.request_fit()
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

    # ------------------------------------------------------------------ editing
    def editor_event(self, event):
        """Preview a gesture without checkpoints; commit only its final event."""
        try:
            op = event["op"]
            if op == "view":
                self.view_event(event["key"], event["value"])
                return
            if op == "command":
                if event["name"] not in COMMANDS:
                    raise ValueError("Unknown editor command")
                # Leave the WebChannel callback before opening a modal file dialog.
                QtCore.QTimer.singleShot(0, getattr(self, event["name"]))
                return
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

    def view_event(self, key, value):
        """Legend chips and graph toggles; display-only, never part of undo history."""
        if key in self.curve_checks and type(value) is bool:
            self.curve_checks[key].setChecked(value)
        elif key == "spl" and type(value) is bool:
            self.relative_check.setChecked(not value)
        elif key == "dac_mode" and value in self.dac_mode_actions:
            self.dac_mode_actions[value].setChecked(True)
        elif key == "raw_nodes" and type(value) is bool:
            self.session.view_options["raw_nodes"] = value
            self.visibility_changed()
        else:
            raise ValueError("Unknown view option")

    def add_filter(self):
        doc = copy.deepcopy(self.session.document)
        new_id = max((f.id for f in doc.filters), default=0)+1
        frequency = 1000.
        while any(abs(f.frequency-frequency) < 1 for f in doc.filters) and frequency < 16000:
            frequency *= 1.25
        doc.filters.append(Filter(new_id, frequency, 0, .7))
        self.set_document(doc)

    def remove_filter(self):
        row_index = self.filters_table.currentRow()
        if row_index >= 0:
            doc = copy.deepcopy(self.session.document)
            del doc.filters[row_index]
            self.set_document(doc)

    def edit_filter(self, item):
        row_index = item.row()
        doc = copy.deepcopy(self.session.document)
        try:
            entries = [self.filters_table.item(row_index, i) for i in range(6)]
            doc.filters[row_index] = Filter(int(entries[1].text()), float(entries[3].text()),
                                            float(entries[4].text()), float(entries[5].text()),
                                            entries[2].text().upper(),
                                            entries[0].checkState() == QtCore.Qt.CheckState.Checked)
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

    # ------------------------------------------------------------------ presentation
    def _update_chrome(self, doc, bank, key, has_measurement, has_target, plan_count):
        writable = bool(bank)
        pending = bool(self.session.firmware_plans or self.session.metadata_edits)
        self.fit_action.setEnabled(writable)
        self.metadata_action.setEnabled(writable)
        self.export_action.setEnabled(writable and pending)
        self.clear_action.setEnabled(pending)
        self.copy_preset_action.setEnabled(writable)
        self.mapping_action.setEnabled(bool(self.firmware))
        self.scan_action.setEnabled(bool(self.firmware))
        self.firmware_fit_button.setEnabled(writable and bool(doc.raw or doc.filters))
        self.export_button.setEnabled(writable and pending)
        self.firmware_hint.setVisible(not writable)
        self.firmware_open_button.setVisible(not writable)
        self.firmware_body.setVisible(writable)
        self.reference_block.setVisible(writable)
        self.fit_target_button.setEnabled(has_measurement and has_target)
        if not self._online_manual:
            # Online pickers stay open while nothing is measured, then fold away to keep the rail short.
            self._restoring_online = True
            self.online_toggle.setChecked(not has_measurement)
            self.online_toggled(not has_measurement)
            self._restoring_online = False
        for name, preset_button in self.preset_buttons.items():
            preset_button.setChecked(name == key)
        special = bool(bank and key and config(bank, key).get("special"))
        self.state_combo.setEnabled(bool(bank) and not special)
        if self.firmware:
            name = Path(self.firmware.path).name
            text = f"{name} · {'已识别 '+bank['configuration'].get('adapter', '') if bank else '未识别布局（只读）'}"
            mark(self.firmware_label, "chip", "ok" if bank else "warn")
            self.firmware_label.setToolTip(f"{self.firmware.path}\nSHA-256 {self.firmware.sha256}\n{self.firmware.recognition}")
        else:
            text = "未打开固件"
            mark(self.firmware_label, "chip", None)
            self.firmware_label.setToolTip("没有固件也可以导入测量、生成和编辑 EQ")
        self.firmware_label.setText(text)
        self.undo_action.setEnabled(bool(self.session._undo))
        self.redo_action.setEnabled(bool(self.session._redo))
        for action, history, title in ((self.undo_action, self.session._undo, "撤销"),
                                       (self.redo_action, self.session._redo, "重做")):
            action.setToolTip(title+("："+history[-1]["action"] if history else ""))
        # Rebuild the plan list only when plans change, so a selection survives live previews.
        plans = [(p["destination"], p["eq"]["name"], len(p["records"])) for p in self.session.firmware_plans]
        if plans != self._plans_shown:
            self._plans_shown = plans
            self.plans_list.clear()
            for destination, name, count in plans:
                item = QtWidgets.QListWidgetItem(f"{destination} ← {name or '复制的预设'} · {count} 条")
                item.setData(QtCore.Qt.ItemDataRole.UserRole, destination)
                self.plans_list.addItem(item)
        self.remove_plan_button.setEnabled(bool(self.session.firmware_plans))
        queued = "、".join(p["destination"] for p in self.session.firmware_plans)
        edits = len(self.session.metadata_edits)
        self.firmware_edits_label.setText(("待写入：" + (queued or "无预设") + f" · 元数据 {edits} 项") if pending
                                          else "尚无待导出的修改。")
        # Card summaries tell where the workflow stands at a glance.
        m = self.measurement_combo.currentIndex()
        t = self.target_combo.currentIndex()
        self.cards["measurement"].set_summary(self.measurements[m].title if has_measurement else "未选择", has_measurement)
        self.cards["target"].set_summary(self.targets[t].title if has_target else "未选择", has_target)
        self.cards["correction"].set_summary(
            f"{doc.name} · RAW {len(doc.raw)} 点 · PEQ {len(doc.filters)} 个" if doc.raw or doc.filters
            else "尚无修正：生成、导入或在图上双击添加", bool(doc.raw or doc.filters))
        if bank and key:
            self.cards["firmware"].set_summary(f"基底：{config(bank, key)['title']} · 计划 {plan_count} 项",
                                               bool(plan_count))
        else:
            self.cards["firmware"].set_summary("未打开已识别的固件" if not self.firmware else "布局未识别，只读",
                                               warn=bool(self.firmware))
        self.cards["export"].set_summary("有待导出的修改" if pending else "", pending)
        if self.fit_report:
            text = " · ".join(f"{int(rate)/1000:g} kHz RMS {v['rms_db']:.2f} / 最大 {v['max_db']:.2f} dB"
                              for rate, v in self.fit_report["rates"].items())
            self.quality_label.setText("相对平滑／限幅后的期望修正："+text)
        self.quality_label.setVisible(bool(self.fit_report))
        if self.firmware:
            text = json.dumps({"original": self.firmware.package["summary"],
                               "configuration": bank["configuration"] if bank else None,
                               "pending_presets": [{"destination": p["destination"], "baseline": p.get("baseline"),
                                                    "records": len(p["records"])} for p in self.session.firmware_plans],
                               "pending_metadata": self.session.metadata_edits}, ensure_ascii=False, indent=2)
            if text != self._metadata_json:
                self._metadata_json = text
                self.metadata_text.setPlainText(text)

    def _update_tables(self, doc, records):
        self.eq_label.setText(f"{doc.name} · RAW {len(doc.raw)} 点 · PEQ {len(doc.filters)} 个")
        self.filters_table.blockSignals(True)
        self.filters_table.setRowCount(len(doc.filters))
        for row_index, f in enumerate(doc.filters):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.CheckState.Checked if f.enabled else QtCore.Qt.CheckState.Unchecked)
            self.filters_table.setItem(row_index, 0, check)
            for column, value in enumerate((f.id, f.kind, f.frequency, f.gain, f.q), 1):
                self.filters_table.setItem(row_index, column, QtWidgets.QTableWidgetItem(str(value)))
        self.filters_table.blockSignals(False)
        self._peq_map = [i for i, f in enumerate(doc.filters) if f.enabled]
        self.firmware_table.setRowCount(sum(r["count"] for _, r in records))
        row_index = 0
        gains = []
        for role, record in records:
            gains.append(f"DAC{role['output']}: gain0 {record['gain0']:.4g} / gain1 {record['gain1']:.4g} dB")
            for f in record["slots"][:record["count"]]:
                kind = FIRMWARE_KINDS.get(f["type_id"], FILTER_TYPES.get(f["type_id"], str(f["type_id"])))
                for column, value in enumerate((f"DAC{role['output']}", kind, f"{f['gain']:.3g}",
                                                f"{f['fc']:.6g}", f"{f['q']:.4g}")):
                    self.firmware_table.setItem(row_index, column, QtWidgets.QTableWidgetItem(value))
                row_index += 1
        self.gains_label.setText("\n".join(gains) or "固件滤波器和增益字段只读；通过拟合或粘贴生成修改计划。")

    def refresh(self, *_):
        if not hasattr(self, "gains_label"):
            return
        doc = self._preview_document or self.session.document
        bank = self.firmware.profiles if self.firmware else None
        key = self.current_key()
        colors = self.theme["curves"]
        special = bool(bank and key and config(bank, key).get("special"))
        frequency = np.geomspace(20, 20000, 2048)
        fs = int(self.rate_combo.currentText())
        state = 0 if special else max(0, self.state_combo.currentIndex())
        raw_doc = copy.deepcopy(doc)
        raw_doc.filters = []
        raw_values, eq_values = correction(raw_doc, frequency, fs), correction(doc, frequency, fs)
        self.main_plot.clear_curves()
        difference, difference_error = None, ""
        try:
            difference = self.difference(frequency)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            difference_error = str(exc)
        records = []
        plan = next((p for p in self.session.firmware_plans if p["destination"] == key), None)
        if plan is None:
            plan = next((p for p in reversed(self.session.firmware_plans) if p.get("baseline") == key), None)
        if bank and key:
            for role in sorted(tables_for(bank, self.region_combo.currentText()), key=lambda r: r["output"]):
                record = record_at(bank, role["name"], key, state)
                records.append((role, record))
                if self.curve_checks["dac"+role["output"]].isChecked():
                    color = colors["dac"+role["output"]]
                    show_difference = self.dac_mode() == "difference"
                    if show_difference and difference:
                        self.main_plot.plot(frequency, difference["branches"][role["output"]], color=color,
                            name=f"DAC{role['output']} · 相对参考差分")
                    elif not show_difference:
                        self.main_plot.plot(frequency, firmware_curve(record, frequency, fs), color=color,
                            name=f"DAC{role['output']} · 当前滤波增益")
                    if plan and not show_difference:
                        planned = next(c["record"] for c in plan["records"]
                                       if c["table"] == role["name"] and c["state"] == state)
                        self.main_plot.plot(frequency, firmware_response(planned["filters"], frequency, fs), color=color,
                            dashed=True, name=f"DAC{role['output']} · 待导出 {plan['destination']}")
        base, spl_offset, bands = np.zeros_like(frequency), 0., []
        measurement_index = self.measurement_combo.currentIndex()
        has_measurement = 0 <= measurement_index < len(self.measurements)
        target_index = self.target_combo.currentIndex()
        has_target = 0 <= target_index < len(self.targets)
        planned_curve = False
        if has_measurement:
            m = self.measurements[measurement_index]
            x, values = np.asarray(m.frequencies), np.asarray(m.spl_values)
            spl_offset = float(np.interp(np.log(1000), np.log(x), values))
            values = values-spl_offset
            base = np.interp(np.log(frequency), np.log(x), values)
            visible = (frequency >= x[0]) & (frequency <= x[-1])
            if self.curve_checks["original"].isChecked():
                self.main_plot.plot(x, values, color=colors["original"], name="原始实测")
            if difference:
                base += difference["delta"]
                if self.curve_checks["current"].isChecked():
                    self.main_plot.plot(frequency[visible], base[visible], color=colors["current"], dashed=True,
                                        name="当前固件估计")
                if difference["uncertain_band"]:
                    bands.append({"low": difference["uncertain_band"][0], "high": difference["uncertain_band"][1],
                                  "label": "双单元交叠 · 模型近似"})
            if self.curve_checks["estimated"].isChecked():
                self.main_plot.plot(frequency[visible], (base+eq_values)[visible], color=colors["estimated"],
                                    name="编辑后估计", width=2.5)
            if plan:
                try:
                    pending = self.difference(frequency, [plan], plan["destination"])
                    if pending:
                        planned_curve = True
                        if self.curve_checks["planned"].isChecked():
                            original = np.interp(np.log(frequency), np.log(x), values)
                            self.main_plot.plot(frequency[visible], (original+pending["delta"])[visible],
                                color=colors["planned"], dashed=True, name=f"待导出估计 · {plan['destination']}")
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    difference_error = str(exc)
        if has_target and self.curve_checks["target"].isChecked():
            target = self.targets[target_index]
            target_values = np.asarray(target.spl_values)
            target_values = target_values-np.interp(np.log(1000), np.log(target.frequencies), target_values)
            self.main_plot.plot(target.frequencies, target_values, color=colors["target"], dashed=True,
                                name="目标频响 · 1kHz 对齐")
        show_correction = self.curve_checks["correction"].isChecked() or not has_measurement
        if show_correction and (doc.raw or doc.filters):
            self.main_plot.plot(frequency, eq_values, color=colors["correction"], name="修正 EQ · 0dB 基准")
        self._update_tables(doc, records)
        self._update_chrome(doc, bank, key, has_measurement, has_target, len(self.session.firmware_plans))
        raw_nodes = self.session.view_options.get("raw_nodes", len(doc.raw) <= 64)
        legend = self._legend(doc, bank, has_measurement, has_target, bool(difference), planned_curve,
                              show_correction, raw_nodes)
        self.main_plot.setLabel("left", "dB · EQ 增益／相对声压")
        self.main_plot.set_editor(
            label=f"调音 · {fs/1000:g} kHz"+(f" · {self.region_combo.currentText()}" if bank else ""),
            filters=[asdict(f) for f in doc.filters], raw=doc.raw, rawNodes=bool(raw_nodes),
            nodeBase=np.column_stack((frequency, base+raw_values)).tolist(),
            rawBase=np.column_stack((frequency, base)).tolist(),
            addBase=np.column_stack((frequency, base+eq_values)).tolist(), bands=bands,
            splOffset=spl_offset if has_measurement and not self.relative_check.isChecked() else None,
            legend=legend, notice=self._notice(difference, difference_error, has_measurement),
            dacMode={"value": self.dac_mode(), "options": [{"value": k, "label": t} for k, t in DAC_MODES]}
            if bank and (self.curve_checks["dac1"].isChecked() or self.curve_checks["dac2"].isChecked()) else None,
            empty="还没有曲线：导入或在线选择原始测量，或者在图上双击添加 PEQ。",
            actions=[{"label": "导入测量…", "command": "import_measurements"},
                     {"label": "导入 EQ…", "command": "import_eq"},
                     {"label": "打开固件…", "command": "open_firmware"}])

    def _legend(self, doc, bank, has_measurement, has_target, has_difference, planned_curve, show_correction,
                raw_nodes):
        colors = self.theme["curves"]
        items = []

        def add(key, title, group, checked, dashed=False, hint=None, color=None, **extra):
            items.append({"key": key, "label": title, "group": group, "checked": bool(checked), "dashed": dashed,
                          "color": color or colors.get(key, self.theme["tokens"]["muted"]), "hint": hint, **extra})
        checks = {key: action.isChecked() for key, action in self.curve_checks.items()}
        if has_measurement:
            add("original", "原始实测", "acoustic", checks["original"], hint="人工耳实测，1 kHz 对齐")
            if has_difference:
                add("current", "当前固件估计", "acoustic", checks["current"], True,
                    "实测 + 当前固件相对测量参考的数字差分（模型估计）")
            add("estimated", "编辑后估计", "acoustic", checks["estimated"], hint="估计值，不是实测")
            if planned_curve:
                add("planned", "待导出估计", "acoustic", checks["planned"], True, "按待写入的固件参数计算的估计")
        if has_target:
            add("target", "目标", "acoustic", checks["target"], True)
        if doc.raw or doc.filters:
            add("correction", "修正 EQ", "digital", show_correction, hint="数字滤波增益，0 dB 为基准"
                if has_measurement else "没有实测时总是显示修正曲线", locked=not has_measurement)
        if bank:
            for output in ("1", "2"):
                add("dac"+output, f"DAC{output}", "digital", checks["dac"+output],
                    hint=f"输出{output} 的数字滤波链；仅控制显示，不改变最终拟合覆盖范围")
        if doc.raw:
            add("raw_nodes", f"RAW 节点 {len(doc.raw)}", "edit", raw_nodes, color=self.theme["tokens"]["raw-node"],
                dot=True, hint="显示可拖动的 GraphicEQ 节点")
        if has_measurement:
            add("spl", "SPL 刻度", "axis", not self.relative_check.isChecked(), color=self.theme["tokens"]["muted"],
                hint="在右侧显示绝对声压刻度")
        return items

    def _notice(self, difference, difference_error, has_measurement):
        if difference and has_measurement:
            assumed = self.reference_assumed.isChecked()
            mode = difference["mode"]
            short = mode.split("；")[0].split("，")[0]
            return {"text": ("假定参考 · " if assumed else "")+short,
                    "detail": ("参考关系为假定；" if assumed else "")+mode+"。编辑后估计 = 当前固件估计 + 修正；"
                              "橙色虚线来自待写入参数。所有估计都不是耳机实测。",
                    "tone": "warn" if assumed or "近似" in mode else "info"}
        if difference_error:
            return {"text": "固件差分未计算", "detail": difference_error+"。编辑后曲线暂为实测＋修正。", "tone": "warn"}
        if has_measurement:
            return {"text": "编辑后估计 = 实测＋修正",
                    "detail": "绑定参考固件后加入当前固件差分；所有曲线共享频率轴和 dB 比例。", "tone": "info"}
        return None

    def closeEvent(self, event):
        if self.workers or self.network_workers:
            self.statusBar().showMessage("正在读取文件，完成后即可关闭窗口。")
            event.ignore()
        else:
            event.accept()
