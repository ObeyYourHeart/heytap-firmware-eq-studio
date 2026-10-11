"""What the unified graph shows: curves, legend and model notice for one window state; no Qt.

The window gathers widget state into a View; compose() returns a Scene the window draws.
Rules kept here: measurement and target are aligned at 1 kHz, the edited estimate is
measurement + firmware difference (when bound) + correction, and every estimate is
labelled as an estimate rather than a measurement.
"""

from dataclasses import dataclass, field

import numpy as np

from heytap_eq.configuration import config, record_at, tables_for
from heytap_eq.dsp import correction, firmware_curve
from heytap_eq.firmware_dsp import response as firmware_response

DIFFERENCE_ERRORS = (ValueError, KeyError, IndexError, TypeError)


@dataclass
class View:
    doc: object  # the correction shown (the live document while dragging)
    rate: int
    checks: dict  # curve key -> visible
    colors: dict  # theme["curves"]
    tokens: dict  # theme["tokens"]
    measurement: object = None
    target: object = None
    preview: object = None  # generated correction that is not applied yet
    bank: object = None
    key: str | None = None
    state_index: int = 0
    region: str = ""
    plans: list = field(default_factory=list)
    dac_mode: str = "chain"
    difference: object = None  # difference(frequency, plans=(), current_key=None) -> dict | None
    relative: bool = False
    raw_nodes: bool = True
    reference_assumed: bool = False


@dataclass
class Scene:
    frequency: np.ndarray
    curves: list  # {"x", "y", "color", "name", "dashed", "width"} in drawing order
    legend: list
    notice: dict | None
    bands: list
    records: list  # [(role, record)] of the current preset, for the firmware table
    base: np.ndarray  # measurement (+ firmware difference) relative to 1 kHz; zeros without one
    raw_values: np.ndarray  # RAW part of the correction
    eq_values: np.ndarray  # whole correction
    spl_offset: float | None  # absolute SPL at 1 kHz for the right-hand scale
    state: int
    has_measurement: bool
    has_target: bool


def compose(view, frequency=None):
    frequency = np.geomspace(20, 20000, 2048) if frequency is None else np.asarray(frequency)
    doc, fs, checks, colors = view.doc, view.rate, view.checks, view.colors
    bank, key = view.bank, view.key
    special = bool(bank and key and config(bank, key).get("special"))
    state = 0 if special else max(0, view.state_index)
    raw_only = type(doc)(name=doc.name, raw=doc.raw)
    raw_values, eq_values = correction(raw_only, frequency, fs), correction(doc, frequency, fs)
    curves = []

    def plot(x, y, key, name, dashed=False, width=None, color=None):
        curves.append({"x": x, "y": y, "color": color or colors[key], "name": name, "dashed": dashed,
                       "width": width})

    difference, difference_error = None, ""
    try:
        difference = view.difference(frequency) if view.difference else None
    except DIFFERENCE_ERRORS as exc:
        difference_error = str(exc)
    plan = next((p for p in view.plans if p["destination"] == key), None)
    if plan is None:
        plan = next((p for p in reversed(view.plans) if p.get("baseline") == key), None)
    records = []
    if bank and key:
        for role in sorted(tables_for(bank, view.region), key=lambda r: r["output"]):
            record = record_at(bank, role["name"], key, state)
            records.append((role, record))
            output = role["output"]
            if not checks["dac"+output]:
                continue
            if view.dac_mode == "difference":
                if difference:
                    plot(frequency, difference["branches"][output], "dac"+output, f"DAC{output} · 相对参考差分")
                continue
            plot(frequency, firmware_curve(record, frequency, fs), "dac"+output, f"DAC{output} · 当前滤波增益")
            if plan:
                planned = next(c["record"] for c in plan["records"]
                               if c["table"] == role["name"] and c["state"] == state)
                plot(frequency, firmware_response(planned["filters"], frequency, fs), "dac"+output,
                     f"DAC{output} · 待导出 {plan['destination']}", dashed=True)
    base, spl_offset, bands, planned_curve = np.zeros_like(frequency), None, [], False
    measurement, target = view.measurement, view.target
    if measurement is not None:
        x, values = np.asarray(measurement.frequencies), np.asarray(measurement.spl_values)
        spl_offset = float(np.interp(np.log(1000), np.log(x), values))
        values = values-spl_offset
        base = np.interp(np.log(frequency), np.log(x), values)
        visible = (frequency >= x[0]) & (frequency <= x[-1])
        if checks["original"]:
            plot(x, values, "original", "原始实测")
        if difference:
            base = base+difference["delta"]
            if checks["current"]:
                plot(frequency[visible], base[visible], "current", "当前固件估计", dashed=True)
            if difference["uncertain_band"]:
                low, high = difference["uncertain_band"]
                bands.append({"low": low, "high": high, "label": "双单元交叠 · 模型近似"})
        if checks["estimated"]:
            plot(frequency[visible], (base+eq_values)[visible], "estimated", "编辑后估计", width=2.5)
        if view.preview is not None and checks["preview"]:
            preview_values = correction(view.preview, frequency, fs)
            plot(frequency[visible], (base+preview_values)[visible], "preview", "生成预览 · 应用后估计", dashed=True)
        if plan:
            try:
                pending = view.difference(frequency, [plan], plan["destination"]) if view.difference else None
                if pending:
                    planned_curve = True
                    if checks["planned"]:
                        original = np.interp(np.log(frequency), np.log(x), values)
                        plot(frequency[visible], (original+pending["delta"])[visible], "planned",
                             f"待导出估计 · {plan['destination']}", dashed=True)
            except DIFFERENCE_ERRORS as exc:
                difference_error = str(exc)
    if target is not None and checks["target"]:
        target_values = np.asarray(target.spl_values)
        target_values = target_values-np.interp(np.log(1000), np.log(target.frequencies), target_values)
        plot(target.frequencies, target_values, "target", "目标频响 · 1kHz 对齐", dashed=True)
    has_measurement = measurement is not None
    show_correction = checks["correction"] or not has_measurement
    if show_correction and (doc.raw or doc.filters):
        plot(frequency, eq_values, "correction", "修正 EQ · 0dB 基准")
    legend = _legend(view, has_measurement, bool(difference), planned_curve, show_correction)
    return Scene(frequency, curves, legend, _notice(view, difference, difference_error), bands, records, base,
                 raw_values, eq_values, spl_offset if has_measurement and not view.relative else None, state,
                 has_measurement, target is not None)


def _legend(view, has_measurement, has_difference, planned_curve, show_correction):
    doc, checks, colors, tokens = view.doc, view.checks, view.colors, view.tokens
    items = []

    def add(key, title, group, checked, dashed=False, hint=None, color=None, **extra):
        items.append({"key": key, "label": title, "group": group, "checked": bool(checked), "dashed": dashed,
                      "color": color or colors.get(key, tokens["muted"]), "hint": hint, **extra})
    if has_measurement:
        add("original", "原始实测", "acoustic", checks["original"], hint="人工耳实测，1 kHz 对齐")
        if has_difference:
            add("current", "当前固件估计", "acoustic", checks["current"], True,
                "实测 + 当前固件相对测量参考的数字差分（模型估计）")
        add("estimated", "编辑后估计", "acoustic", checks["estimated"], hint="估计值，不是实测")
        if planned_curve:
            add("planned", "待导出估计", "acoustic", checks["planned"], True, "按待写入的固件参数计算的估计")
        if view.preview is not None:
            add("preview", "生成预览", "acoustic", checks["preview"], True,
                "③ 中生成设置的预览，应用前不会改变修正 EQ")
    if view.target is not None:
        add("target", "目标", "acoustic", checks["target"], True)
    if doc.raw or doc.filters:
        add("correction", "修正 EQ", "digital", show_correction, hint="数字滤波增益，0 dB 为基准"
            if has_measurement else "没有实测时总是显示修正曲线", locked=not has_measurement)
    if view.bank:
        for output in ("1", "2"):
            add("dac"+output, f"DAC{output}", "digital", checks["dac"+output],
                hint=f"输出{output} 的数字滤波链；仅控制显示，不改变最终拟合覆盖范围")
    if doc.raw:
        add("raw_nodes", f"RAW 节点 {len(doc.raw)}", "edit", view.raw_nodes, color=tokens["raw-node"],
            dot=True, hint="显示可拖动的 GraphicEQ 节点")
    if has_measurement:
        add("spl", "SPL 刻度", "axis", not view.relative, color=tokens["muted"], hint="在右侧显示绝对声压刻度")
    return items


def _notice(view, difference, difference_error):
    has_measurement = view.measurement is not None
    if difference and has_measurement:
        mode = difference["mode"]
        short = mode.split("；")[0].split("，")[0]
        assumed = view.reference_assumed
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
