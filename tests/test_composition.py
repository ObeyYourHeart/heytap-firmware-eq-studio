"""Curve composition without a window: what the graph shows for a given state."""

import numpy as np
import pytest

from heytap_eq import theme
from heytap_eq.composition import View, compose
from heytap_eq.eq_formats import EQDocument, parse_text
from heytap_eq.measurements import Measurement
from tests.firmware_fixture import synthetic_firmware

GRID = np.geomspace(20, 20000, 512)
CHECKS = {"original": True, "current": True, "estimated": True, "target": True, "planned": True,
          "correction": True, "dac1": False, "dac2": False, "preview": True}


def view(**changes):
    resolved = theme.resolve({"mode": "dark"})
    values = {"doc": EQDocument(), "rate": 48000, "checks": dict(CHECKS), "colors": resolved["curves"],
              "tokens": resolved["tokens"]}
    return View(**{**values, **changes})


def curve(scene, prefix):
    return next(c for c in scene.curves if c["name"].startswith(prefix))


def measurement():
    f = np.geomspace(30, 18000, 300)
    return Measurement("Bumpy", f.tolist(), (90+3*np.exp(-.5*(np.log2(f/60)/.5)**2)).tolist()).validate()


def test_empty_window_shows_nothing_and_needs_no_notice():
    scene = compose(view(), GRID)
    assert scene.curves == [] and scene.legend == [] and scene.notice is None
    assert scene.spl_offset is None and not np.any(scene.base)


def test_correction_alone_is_always_drawn_and_its_legend_entry_locked():
    doc = parse_text("[PEQ]\nPEQ1: 1200 -2 1 PEAK")
    scene = compose(view(doc=doc, checks={**CHECKS, "correction": False}), GRID)
    assert [c["name"] for c in scene.curves] == ["修正 EQ · 0dB 基准"]
    assert scene.legend[0]["key"] == "correction" and scene.legend[0]["locked"] and scene.legend[0]["checked"]
    assert np.min(scene.eq_values) == pytest.approx(-2, abs=.05)


def test_measurement_and_target_align_at_1khz_and_the_estimate_adds_the_correction():
    doc = parse_text("[PEQ]\nPEQ1: 1200 -2 1 PEAK")
    doc.raw = [[20., 0.], [500., 1.5], [20000., 0.]]
    f = np.geomspace(40, 16000, 200)
    target = Measurement("Target", f.tolist(), (75+6/(1+(f/105)**2)).tolist()).validate()
    scene = compose(view(doc=doc, measurement=measurement(), target=target), GRID)
    assert [c["name"] for c in scene.curves] == ["原始实测", "编辑后估计", "目标频响 · 1kHz 对齐", "修正 EQ · 0dB 基准"]
    original, estimated, aligned, _ = scene.curves
    assert np.interp(np.log(1000), np.log(original["x"]), original["y"]) == pytest.approx(0, abs=1e-9)
    assert np.interp(np.log(1000), np.log(aligned["x"]), aligned["y"]) == pytest.approx(0, abs=1e-9)
    assert scene.spl_offset == pytest.approx(90, abs=.01)
    # Only drawn where the measurement exists, and exactly measurement + correction there.
    inside = (GRID >= 30) & (GRID <= 18000)
    assert np.array_equal(estimated["x"], GRID[inside]) and estimated["width"] == 2.5
    np.testing.assert_allclose(estimated["y"], (scene.base+scene.eq_values)[inside])
    assert scene.notice["text"] == "编辑后估计 = 实测＋修正"
    keys = [item["key"] for item in scene.legend]
    assert keys == ["original", "estimated", "target", "correction", "raw_nodes", "spl"]


def test_hidden_curves_keep_their_legend_entries_and_relative_mode_drops_the_spl_scale():
    checks = {**CHECKS, "original": False, "estimated": False}
    scene = compose(view(measurement=measurement(), checks=checks, relative=True), GRID)
    assert scene.curves == [] and scene.spl_offset is None
    legend = {item["key"]: item["checked"] for item in scene.legend}
    assert legend == {"original": False, "estimated": False, "spl": False}


def test_preview_is_an_estimate_beside_the_unchanged_correction():
    preview = parse_text("[PEQ]\nPEQ1: 3000 4 1 PEAK")
    scene = compose(view(measurement=measurement(), preview=preview), GRID)
    shown = curve(scene, "生成预览")
    assert shown["dashed"] and not np.any(scene.eq_values)
    assert np.max(shown["y"]-curve(scene, "编辑后估计")["y"]) == pytest.approx(4, abs=.05)
    assert "preview" in [item["key"] for item in scene.legend]
    # Without a measurement there is nothing to add the preview to.
    assert compose(view(preview=preview), GRID).curves == []


def test_firmware_difference_enters_the_estimate_and_labels_the_model():
    delta = np.full(len(GRID), 1.5)

    def difference(frequency, plans=(), current_key=None):
        return {"delta": delta, "branches": {"1": delta, "2": -delta}, "uncertain_band": (1500, 3000),
                "mode": "双单元近似，功率权重；说明"}

    scene = compose(view(measurement=measurement(), difference=difference, reference_assumed=True), GRID)
    current = curve(scene, "当前固件估计")
    assert current["dashed"]
    inside = (GRID >= 30) & (GRID <= 18000)
    no_firmware = compose(view(measurement=measurement()), GRID)
    np.testing.assert_allclose(current["y"], (no_firmware.base+1.5)[inside])
    assert scene.bands == [{"low": 1500, "high": 3000, "label": "双单元交叠 · 模型近似"}]
    assert scene.notice["text"] == "假定参考 · 双单元近似" and scene.notice["tone"] == "warn"
    assert "不是耳机实测" in scene.notice["detail"]


def test_a_failing_difference_is_reported_instead_of_hiding_the_graph():
    def difference(*_):
        raise ValueError("参考固件缺少该预设")

    scene = compose(view(measurement=measurement(), difference=difference), GRID)
    assert scene.notice == {"text": "固件差分未计算", "detail": "参考固件缺少该预设。编辑后曲线暂为实测＋修正。",
                            "tone": "warn"}
    assert [c["name"] for c in scene.curves] == ["原始实测", "编辑后估计"]


def test_dac_chains_follow_the_mode_and_pending_plans(monkeypatch, tmp_path):
    from heytap_eq.firmware_edit import make_plan

    firmware = synthetic_firmware(monkeypatch, tmp_path)
    bank, key = firmware.profiles, "丹拿原声"
    checks = {**CHECKS, "dac1": True, "dac2": True}
    scene = compose(view(bank=bank, key=key, region="other", checks=checks), GRID)
    assert [c["name"] for c in scene.curves] == ["DAC1 · 当前滤波增益", "DAC2 · 当前滤波增益"]
    assert [role["output"] for role, _ in scene.records] == ["1", "2"]
    assert {"dac1", "dac2"} <= {item["key"] for item in scene.legend}
    # Difference mode shows nothing until a reference difference exists.
    assert compose(view(bank=bank, key=key, region="other", checks=checks, dac_mode="difference"), GRID).curves == []
    plan = make_plan(firmware, parse_text("[PEQ]\nPEQ1: 1200 -2 1 PEAK"), key)
    planned = compose(view(bank=bank, key=key, region="other", checks=checks, plans=[plan]), GRID)
    assert [c["name"] for c in planned.curves] == ["DAC1 · 当前滤波增益", "DAC1 · 待导出 丹拿原声",
                                                   "DAC2 · 当前滤波增益", "DAC2 · 待导出 丹拿原声"]
    assert all(c["dashed"] for c in planned.curves if "待导出" in c["name"])
