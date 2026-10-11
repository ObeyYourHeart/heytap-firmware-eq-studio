"""Window chrome: menus, toolbar, workflow rail, docks, status bar and theme."""


from PySide6 import QtCore, QtGui, QtWidgets

from heytap_eq import theme as themes
from heytap_eq.session import atomic_json
from heytap_eq.web_plot import ResponsePlot
from heytap_eq.widgets import StepCard, apply_palette, button, label, row, write_icons

CURVES = (("original", "原始实测", True), ("current", "当前固件估计", True), ("estimated", "编辑后估计", True),
          ("target", "目标频响", True), ("planned", "待导出估计", True), ("correction", "修正 EQ", False),
          ("dac1", "DAC1（输出1）", False), ("dac2", "DAC2（输出2）", False))
DAC_MODES = (("chain", "滤波链"), ("difference", "相对参考差分"))


class LayoutMixin:
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
