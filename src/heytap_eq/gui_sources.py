"""Measurements and targets: files, the online browser and curve selection."""

import copy

from heytap_eq.estimation import measurement_key
from heytap_eq.measurements import load_measurements


class SourcesMixin:
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
