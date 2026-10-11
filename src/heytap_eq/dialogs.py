"""Modal steps that need explicit confirmation; each returns plain values or None."""

from PySide6 import QtWidgets

from heytap_eq.configuration import config, configurations
from heytap_eq.widgets import label, mark


def _dialog(parent, title, width=420):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(width)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    form = QtWidgets.QFormLayout()
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)
    layout.addLayout(form)
    return dialog, layout, form


def _buttons(dialog, layout, ok="确定"):
    box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                     QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    accept = box.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
    accept.setText(ok)
    accept.setProperty("primary", True)
    box.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    return accept


def _spin(low, high, value, suffix="", decimals=2, step=1.):
    spin = QtWidgets.QDoubleSpinBox()
    spin.setRange(low, high)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


def firmware_fit(parent, bank, baseline_key, preamp_db):
    """Choose baseline, destination and preamp; returns (baseline, destination, preamp) or None."""
    dialog, layout, form = _dialog(parent, "拟合到固件完整预设", 460)
    baseline, destination = QtWidgets.QComboBox(), QtWidgets.QComboBox()
    for preset in configurations(bank):
        baseline.addItem(("特殊 · " if preset.get("special") else "")+preset["title"], preset["key"])
    baseline.setCurrentIndex(max(0, baseline.findData(baseline_key)))
    preamp = _spin(-24, 0, preamp_db, " dB", 1, .5)
    note = label("", "muted")

    def update():
        source = config(bank, baseline.currentData())
        chosen = destination.currentData()
        destination.blockSignals(True)
        destination.clear()
        # Coverage must match: ordinary presets map state by state, special configs one to one.
        for preset in configurations(bank):
            if len(preset["indices"]) == len(source["indices"]) and bool(preset.get("special")) == bool(source.get("special")):
                destination.addItem(preset["title"], preset["key"])
        if chosen is None:
            # Default to writing a different preset so the baseline itself stays untouched.
            others = [destination.itemData(i) for i in range(destination.count())
                      if destination.itemData(i) != baseline.currentData()]
            chosen = others[-1] if others else baseline.currentData()
        destination.setCurrentIndex(max(0, destination.findData(chosen)))
        destination.blockSignals(False)
        count = len(bank["tables"])*len(source["indices"])
        same = destination.currentData() == baseline.currentData()
        note.setText(f"以「{source['title']}」的完整滤波链为基底，叠加当前修正 EQ，写入「{destination.currentText()}」。"
                     f"\n同时处理全部输出、区域和状态，共 {count} 条记录；保留基底的 HP / LP / AP。"
                     + ("\n目标与基底相同：会直接在该预设现有声音上叠加修正。" if same else "")
                     + "\n结果先加入修改计划，导出前可以撤销或移除。")
        mark(note, "muted", "warn" if same else None)

    baseline.currentIndexChanged.connect(update)
    destination.currentIndexChanged.connect(update)
    update()
    form.addRow("基底预设", baseline)
    form.addRow("写入预设", destination)
    form.addRow("整体衰减", preamp)
    layout.addWidget(note)
    _buttons(dialog, layout, "开始拟合")
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None
    return baseline.currentData(), destination.currentData(), preamp.value()


def export_settings(parent, plans, edits, fields):
    """Review pending changes and confirm export; returns (max_rms, max_error) or None."""
    dialog, layout, form = _dialog(parent, "导出固件", 500)
    rows = []
    worst = []
    for plan in plans:
        values = [m for r in plan["records"] for m in r["record"]["metrics"].values()]
        worst += values
        text = f"{plan['destination']} ← {plan['eq']['name'] or '复制的预设'} · {len(plan['records'])} 条"
        if values:
            text += f" · 最差 RMS {max(v['rms_db'] for v in values):.3f} / 最大 {max(v['max_db'] for v in values):.3f} dB"
        rows.append(text)
    names = {f["id"]: f["label"] for f in fields}
    rows += [f"{names.get(key, key)} → {value}" for key, value in edits.items()]
    summary = QtWidgets.QListWidget()
    summary.addItems(rows)
    summary.setMaximumHeight(28+22*min(6, len(rows)))
    layout.insertWidget(0, label("将写入以下修改：", "muted"))
    layout.insertWidget(1, summary)
    rms, peak = _spin(.01, 20, .45, " dB"), _spin(.01, 60, 1.5, " dB")
    for spin in (rms, peak):
        spin.setEnabled(bool(worst))
    form.addRow("允许 RMS 误差", rms)
    form.addRow("允许最大误差", peak)
    layout.addWidget(label("导出时按实际写入的 float32 参数在 44.1 / 48 / 96 kHz 重新计算误差，超出门限会拒绝导出。"
                           "输出为新文件，旁边保存逐路径验证报告。", "muted"))
    confirm = QtWidgets.QCheckBox("我了解：导出文件只经过离线校验，尚未在耳机上实测；原固件不会被修改。")
    layout.addWidget(confirm)
    accept = _buttons(dialog, layout, "选择保存位置…")
    accept.setEnabled(False)
    confirm.toggled.connect(accept.setEnabled)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None
    return rms.value(), peak.value()


def metadata_edits(parent, known, current):
    """Fixed-width metadata editor; returns {field id: new value} or None."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("编辑固件信息")
    dialog.resize(760, 600)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(16, 14, 16, 14)
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    body = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(body)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    scroll.setWidget(body)
    layout.addWidget(scroll, 1)
    inputs = {}
    for field in known:
        entry = QtWidgets.QLineEdit(current.get(field["id"], field["value"]))
        editable = field.get("editable", True)
        entry.setReadOnly(not editable)
        entry.setEnabled(editable)
        if editable:
            inputs[field["id"]] = entry
        title = field["label"]
        if field["kind"] in ("text", "fixed_text"):
            entry.setMaxLength(field["width"])
            title += f"（最多 {field['width']} 字节）"
        form.addRow(title, entry)
    layout.addWidget(label("文本按原字段容量保存；校验值和长度由封包自动计算。软件版本同步容器、SW_VER 和运行时 getter。",
                           "muted"))
    _buttons(dialog, layout, "应用")
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None
    return {f["id"]: inputs[f["id"]].text() for f in known
            if f["id"] in inputs and inputs[f["id"]].text() != f["value"]}
