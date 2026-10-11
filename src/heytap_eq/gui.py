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

from PySide6 import QtCore, QtGui, QtWidgets

from heytap_eq import theme as themes
from heytap_eq.clipboard import MIME, PRESET_SCHEMA, copy_plan, decode_text, tuning_payload
from heytap_eq.composition import View, compose
from heytap_eq.configuration import config, configurations
from heytap_eq.eq_formats import Filter, dump_eq, load_eq
from heytap_eq.firmware_dsp import FILTER_TYPES
from heytap_eq.flowmix import FlowmixClient
from heytap_eq.gui_correction import CorrectionMixin
from heytap_eq.gui_firmware import FirmwareMixin
from heytap_eq.gui_layout import DAC_MODES, LayoutMixin
from heytap_eq.gui_sources import SourcesMixin
from heytap_eq.preferences import BrowserMemory
from heytap_eq.service_profile import builtin_authorization
from heytap_eq.session import Session
from heytap_eq.widgets import mark

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


class MainWindow(LayoutMixin, SourcesMixin, CorrectionMixin, FirmwareMixin, QtWidgets.QMainWindow):
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

    # ------------------------------------------------------------------ view

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

    # ------------------------------------------------------------------ files

    def import_eq(self):
        path = self._choose("导入 EQ", "EQ 文本（Flowmix / GraphicEQ / Equalizer APO）(*.txt);;所有文件 (*)")
        if path:
            self._task(lambda: load_eq(path), self.set_document)

    def set_document(self, doc):
        self._preview_document = None
        self.session.replace(doc)
        self._changed()

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
        self._update_steps()
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
        self._sync_correction_preview()
        doc = self._preview_document or self.session.document
        bank = self.firmware.profiles if self.firmware else None
        key = self.current_key()
        fs = int(self.rate_combo.currentText())
        m, t = self.measurement_combo.currentIndex(), self.target_combo.currentIndex()
        checks = {name: action.isChecked() for name, action in self.curve_checks.items()}
        raw_nodes = self.session.view_options.get("raw_nodes", len(doc.raw) <= 64)
        scene = compose(View(
            doc=doc, rate=fs, checks=checks, colors=self.theme["curves"], tokens=self.theme["tokens"],
            measurement=self.measurements[m] if 0 <= m < len(self.measurements) else None,
            target=self.targets[t] if 0 <= t < len(self.targets) else None,
            preview=self.correction_preview[0] if self.correction_preview else None,
            bank=bank, key=key, state_index=self.state_combo.currentIndex(), region=self.region_combo.currentText(),
            plans=self.session.firmware_plans, dac_mode=self.dac_mode(), difference=self.difference,
            relative=self.relative_check.isChecked(), raw_nodes=raw_nodes,
            reference_assumed=self.reference_assumed.isChecked()))
        self.main_plot.clear_curves()
        for curve in scene.curves:
            self.main_plot.plot(curve["x"], curve["y"], color=curve["color"], name=curve["name"],
                                dashed=curve["dashed"], width=curve["width"])
        self._update_tables(doc, scene.records)
        self._update_chrome(doc, bank, key, scene.has_measurement, scene.has_target, len(self.session.firmware_plans))
        frequency, base = scene.frequency, scene.base
        self.main_plot.setLabel("left", "dB · EQ 增益／相对声压")
        self.main_plot.set_editor(
            label=f"调音 · {fs/1000:g} kHz"+(f" · {self.region_combo.currentText()}" if bank else ""),
            filters=[asdict(f) for f in doc.filters], raw=doc.raw, rawNodes=bool(raw_nodes),
            nodeBase=self.main_plot.offsets(frequency, base+scene.raw_values),
            rawBase=self.main_plot.offsets(frequency, base),
            addBase=self.main_plot.offsets(frequency, base+scene.eq_values), bands=scene.bands,
            splOffset=scene.spl_offset, legend=scene.legend, notice=scene.notice,
            dacMode={"value": self.dac_mode(), "options": [{"value": k, "label": t} for k, t in DAC_MODES]}
            if bank and (checks["dac1"] or checks["dac2"]) else None,
            empty="还没有曲线：导入或在线选择原始测量，或者在图上双击添加 PEQ。",
            actions=[{"label": "导入测量…", "command": "import_measurements"},
                     {"label": "导入 EQ…", "command": "import_eq"},
                     {"label": "打开固件…", "command": "open_firmware"}])

    def closeEvent(self, event):
        if self.workers or self.network_workers:
            self.statusBar().showMessage("正在读取文件，完成后即可关闭窗口。")
            event.ignore()
        else:
            self.discard_correction_preview()
            if self.preview_worker is not None:
                self.preview_worker.wait(3000)
            event.accept()
