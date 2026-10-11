"""Firmware context, preset fitting, metadata and export."""

import copy
import json
import threading
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from heytap_eq import dialogs, metadata
from heytap_eq.adapters import inspect_firmware
from heytap_eq.clipboard import preset_payload
from heytap_eq.configuration import config, configurations, regions
from heytap_eq.discovery import discover
from heytap_eq.estimation import (
    builtin_reference,
    estimate_difference,
    measurement_key,
    reference_snapshot,
)
from heytap_eq.firmware_edit import export_firmware, make_plan
from heytap_eq.preferences import device_identity
from heytap_eq.session import atomic_json
from heytap_eq.widgets import button, label


class FirmwareMixin:
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

    def copy_preset(self):
        if not self.firmware or not self.firmware.profiles or not self.current_key():
            self.statusBar().showMessage("先打开固件并选择要复制的配置。")
            return
        payload = preset_payload(self.firmware, self.current_key(), self.session.firmware_plans)
        self._set_clipboard(payload)
        self.statusBar().showMessage(f"已复制 {payload['title']} 的完整配置：{len(payload['records'])} 条记录。")

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
