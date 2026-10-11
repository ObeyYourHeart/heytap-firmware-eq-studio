import threading

from PySide6 import QtCore, QtWidgets

from heytap_eq import theme
from heytap_eq.eq_formats import parse_text
from heytap_eq.gui import MainWindow
from heytap_eq.measurements import Measurement


def pump(delay):
    # QTest.qWait keeps the GIL in PySide6 6.8 and starves Python worker threads (a 0.3 s
    # background fit took over 60 s); a nested event loop releases it as app.exec() does.
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(delay, loop.quit)
    loop.exec()


def wait(app, condition, steps=200, delay=25):
    for _ in range(steps):
        app.processEvents()
        if condition():
            return True
        pump(delay)
    return condition()


def test_graph_takes_the_height_freed_by_the_workflow_rail(tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"height.json")
    window.connect_flowmix = lambda: None
    window.show()
    window.resize(1028, 749)
    assert wait(qt_app, lambda: window.main_plot.is_ready)
    window.set_measurements([Measurement("Flat", [20, 1000, 20000], [80, 90, 80]).validate()])
    received = []
    # The page applies payloads asynchronously; wait until the measurement's legend has arrived.
    assert wait(qt_app, lambda: (window.main_plot.inspect(received.append), received and received[-1]
                                 and received[-1]["height"] > 0 and received[-1]["legend"] >= 2)[1], delay=50)
    web = received[-1]
    # The previous layout left 253-258 px for the graph at this size.
    assert web["height"] >= 420, web
    assert web["schema"] == 2 and web["legend"] >= 2
    # 1028 px is below the rail threshold: the rail folds into the step strip and the graph widens.
    assert not window.advanced_controls.isVisible()
    assert not window.rail.isVisible() and window.rail_strip.isVisible()
    assert web["width"] >= 850, web
    window.close()


def test_theme_follows_preferences_and_restyles_both_halves(tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"theme.json")
    window.set_appearance(mode="light", accent="violet")
    light = theme.resolve({"mode": "light", "accent": "violet"})
    assert window.main_plot.theme["mode"] == "light"
    assert window.main_plot.theme["tokens"]["accent"] == light["tokens"]["accent"]
    assert light["tokens"]["bg"] in window.styleSheet()
    icons = sorted((tmp_path/"theme-icons").glob("*.svg"))
    assert icons and all(i.as_posix() in window.styleSheet() for i in icons if light["tokens"]["muted"][1:] in i.name)
    assert window.mode_actions["light"].isChecked() and window.accent_actions["violet"].isChecked()
    window.set_measurements([Measurement("Flat", [20, 1000, 20000], [80, 90, 80]).validate()])
    assert {c["color"] for c in window.main_plot.curves} <= set(light["curves"].values())
    window.close()
    restored = MainWindow(recover=False, auto_path=tmp_path/"theme.json")
    assert restored.appearance == {"mode": "light", "accent": "violet", "contrast": "system"}
    restored.set_appearance(mode="system")
    expected = "dark" if restored.system_is_dark() else "light"
    assert restored.main_plot.theme["mode"] == expected
    restored.close()


def test_high_contrast_follows_the_preference_and_the_system(monkeypatch, tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"contrast.json")
    window.set_appearance(mode="dark", contrast="high")
    assert window.theme["contrast"] == "high" and window.contrast_actions["high"].isChecked()
    assert window.main_plot.theme["contrast"] == "high" and window.main_plot.theme["lineScale"] == 1.5
    assert "border-width: 2px" in window.styleSheet() and window.theme["tokens"]["bg"] == "#000000"
    # Follow the system: Windows contrast themes arrive as a platform ThemeChange event.
    system = {"dark": True, "high": False}
    monkeypatch.setattr(window, "system_appearance", lambda: (system["dark"], system["high"]))
    window.set_appearance(mode="system", contrast="system")
    assert window.theme["contrast"] == "standard" and window.theme["mode"] == "dark"
    applied = []
    apply_theme = window.apply_theme
    monkeypatch.setattr(window, "apply_theme", lambda: (applied.append(1), apply_theme()))
    theme_change = QtCore.QEvent(QtCore.QEvent.Type.ThemeChange)
    QtWidgets.QApplication.sendEvent(window, theme_change)
    assert applied == []  # nothing changed, so no restyle
    system.update(dark=False, high=True)  # e.g. the "Desert" contrast theme
    QtWidgets.QApplication.sendEvent(window, theme_change)
    assert applied == [1] and (window.theme["mode"], window.theme["contrast"]) == ("light", "high")
    assert window.main_plot.theme["tokens"]["bg"] == "#ffffff"
    # An explicit choice is not overridden by the system.
    window.set_appearance(mode="dark", contrast="standard")
    applied.clear()
    system.update(dark=False, high=False)
    QtWidgets.QApplication.sendEvent(window, theme_change)
    assert applied == [] and (window.theme["mode"], window.theme["contrast"]) == ("dark", "standard")
    window.close()


def test_legend_view_events_and_commands_are_validated(tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"legend.json")
    window.set_document(parse_text("GraphicEQ: 20 1; 1000 2; 20000 0"))
    window.set_measurements([Measurement("Flat", [20, 1000, 20000], [80, 90, 80]).validate()])
    window.set_targets([Measurement("Target", [20, 1000, 20000], [2, 0, -1]).validate()])
    legend = {item["key"]: item for item in window.main_plot.editor["legend"]}
    assert {"original", "estimated", "target", "correction", "raw_nodes", "spl"} <= set(legend)
    assert "dac1" not in legend and "current" not in legend
    history = len(window.session._undo)
    window.editor_event({"op": "view", "key": "target", "value": False})
    assert not window.curve_checks["target"].isChecked()
    assert all("目标" not in c["name"] for c in window.main_plot.curves)
    window.editor_event({"op": "view", "key": "raw_nodes", "value": False})
    assert window.main_plot.editor["rawNodes"] is False
    window.editor_event({"op": "view", "key": "spl", "value": True})
    assert window.main_plot.editor["splOffset"] is not None
    assert len(window.session._undo) == history
    window.editor_event({"op": "view", "key": "bogus", "value": True})
    assert "未应用" in window.statusBar().currentMessage()
    window.editor_event({"op": "command", "name": "close"})
    assert "未应用" in window.statusBar().currentMessage()
    calls = []
    window.import_measurements = lambda: calls.append("import")
    window.editor_event({"op": "command", "name": "import_measurements"})
    assert wait(qt_app, lambda: calls == ["import"])
    window.close()


def test_firmware_actions_follow_state_and_plan_removal_is_undoable(monkeypatch, tmp_path, qt_app):
    from tests.firmware_fixture import synthetic_firmware

    window = MainWindow(recover=False, auto_path=tmp_path/"actions.json")
    window.connect_flowmix = lambda: None
    window.show()
    assert not window.fit_action.isEnabled() and not window.export_action.isEnabled()
    # Relative to the rail: on small screens the window opens below the fold width and the rail is hidden.
    rail = window.rail
    assert window.firmware_hint.isVisibleTo(rail) and not window.firmware_body.isVisibleTo(rail)
    window.set_firmware(synthetic_firmware(monkeypatch, tmp_path))
    assert window.fit_action.isEnabled() and not window.export_action.isEnabled()
    assert window.firmware_body.isVisibleTo(rail) and window.reference_block.isVisibleTo(rail)
    assert "special:45" in window.preset_buttons
    window.set_document(parse_text("[PEQ]\nPEQ1: 1200 -2 1 PEAK"))
    window.fit_firmware_preset("清亮高音")
    assert wait(qt_app, lambda: window.session.firmware_plans and not window.workers, steps=400)
    assert window.export_action.isEnabled() and window.plans_list.count() == 1
    assert not window.busy.isVisible() and not window.cancel_button.isVisible()
    window.plans_list.setCurrentRow(0)
    window.remove_selected_plan()
    assert not window.session.firmware_plans and not window.export_action.isEnabled()
    window.undo()
    assert len(window.session.firmware_plans) == 1 and window.plans_list.count() == 1
    rows = [window.firmware_table.item(r, 1).text() for r in range(window.firmware_table.rowCount())]
    assert rows and set(rows) <= {"LS", "PEAK", "HS", "LP", "HP", "AP"}
    window.close()


def test_busy_indicator_and_cancel_only_show_while_working(tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"busy.json")
    window.show()
    release = threading.Event()
    window._task(lambda: release.wait(3), lambda _: None)
    assert window.busy.isVisible() and window.cancel_button.isVisible()
    release.set()
    assert wait(qt_app, lambda: not window.workers)
    assert not window.busy.isVisible() and not window.cancel_button.isVisible()
    window.close()


def test_published_payload_stays_compact(monkeypatch, tmp_path, qt_app):
    import json
    from pathlib import Path

    from heytap_eq.eq_formats import load_eq
    from tests.firmware_fixture import synthetic_firmware

    window = MainWindow(recover=False, auto_path=tmp_path/"payload.json")
    window.connect_flowmix = lambda: None
    assert window.main_plot.payload()["nodeBase"] == []
    window.set_firmware(synthetic_firmware(monkeypatch, tmp_path))
    window.set_document(load_eq(Path("research/enco_x4_eq_toolkit/examples/Technics-AZ80-Optimized.txt")))
    frequency = [20*1.0116**i for i in range(600)]
    window.set_measurements([Measurement("Dense", frequency, [80+i % 7 for i in range(600)]).validate()])
    window.set_targets([Measurement("Target", [20, 1000, 20000], [2, 0, -1]).validate()])
    window.curve_checks["dac1"].setChecked(True)
    window.curve_checks["dac2"].setChecked(True)
    payload = window.main_plot.payload()
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    # Before rounding and the 2048-point grid this configuration published about 1.2 MB per refresh.
    assert len(text) < 350_000, len(text)
    values = [v for curve in payload["curves"] for point in curve["points"] for v in point]
    assert all(round(v, 3) == v for v in values)
    window.close()


def test_rail_folds_on_narrow_windows_and_steps_reopen_it(tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"rail.json")
    window.connect_flowmix = lambda: None
    window.show()
    qt_app.processEvents()
    window._apply_rail_mode(1500)
    assert window.rail.isVisible() and not window.rail_strip.isVisible() and window.rail_action.isChecked()
    window._apply_rail_mode(1000)
    assert not window.rail.isVisible() and window.rail_strip.isVisible() and not window.rail_action.isChecked()
    window.step_buttons["correction"].click()
    assert window.rail.isVisible() and window.cards["correction"].body.isVisible()
    # A manual choice holds until the window crosses the threshold again.
    window._apply_rail_mode(1010)
    assert window.rail.isVisible()
    window._apply_rail_mode(1500)
    window._apply_rail_mode(1000)
    assert not window.rail.isVisible()
    window.rail_action.setChecked(True)
    assert window.rail.isVisible() and not window.rail_strip.isVisible()
    window.set_measurements([Measurement("Flat", [20, 1000, 20000], [80, 90, 80]).validate()])
    assert window.step_buttons["measurement"].property("state") == "ok"
    assert window.step_buttons["target"].property("state") == ""
    window.close()


def test_inline_generator_previews_without_editing_and_applies_once(tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"generator.json")
    window.connect_flowmix = lambda: None
    window.fit_target_button.setChecked(True)
    assert "选择原始频响与目标频响" in window.preview_status.text()
    window.set_measurements([Measurement("Flat", [20, 1000, 20000], [80, 80, 80]).validate()])
    window.set_targets([Measurement("Target", [20, 1000, 20000], [2, 0, -1]).validate()])
    assert wait(qt_app, lambda: window.correction_preview is not None)
    history = len(window.session._undo)
    assert not window.session.document.raw and not window.session.document.filters
    assert any(c["name"].startswith("生成预览") for c in window.main_plot.curves)
    assert "preview" in {item["key"] for item in window.main_plot.editor["legend"]}
    window.apply_correction_preview()
    assert len(window.session.document.raw) >= 127 and window.fit_report
    assert len(window.session._undo) == history+1
    assert window.correction_preview is None
    assert not any(c["name"].startswith("生成预览") for c in window.main_plot.curves)
    window.fit_mode.setCurrentIndex(window.fit_mode.findData("PEQ"))
    assert wait(qt_app, lambda: window.correction_preview is not None and window.correction_preview[0].filters,
                steps=400)
    assert window.session.document.raw and not window.session.document.filters
    # A result computed for older inputs is discarded instead of being shown or applied.
    stale = window._preview_sig
    window.set_targets([Measurement("Other", [20, 1000, 20000], [-2, 0, 3]).validate()])
    assert window._preview_sig != stale and window.correction_preview is None
    window._preview_result(stale, window.session.document and (window.session.document, {"rates": {}}))
    assert window.correction_preview is None
    window.fit_low.setValue(5000)
    window.fit_high.setValue(1000)
    assert "起始频率" in window.preview_status.text() and not window.apply_preview_button.isEnabled()
    window.fit_target_button.setChecked(False)
    assert window.correction_preview is None and not window.generator_panel.isVisibleTo(window.rail)
    window.undo()  # the later target change
    assert window.session.document.raw
    window.undo()  # applying the preview was one global history step
    assert not window.session.document.raw
    window.close()
