import threading

from PySide6 import QtTest

from heytap_eq import theme
from heytap_eq.eq_formats import parse_text
from heytap_eq.gui import MainWindow
from heytap_eq.measurements import Measurement


def wait(app, condition, steps=200, delay=25):
    for _ in range(steps):
        app.processEvents()
        if condition():
            return True
        QtTest.QTest.qWait(delay)
    return condition()


def test_graph_takes_the_height_freed_by_the_workflow_rail(tmp_path, qt_app):
    window = MainWindow(recover=False, auto_path=tmp_path/"height.json")
    window.connect_flowmix = lambda: None
    window.show()
    window.resize(1028, 749)
    assert wait(qt_app, lambda: window.main_plot.is_ready)
    window.set_measurements([Measurement("Flat", [20, 1000, 20000], [80, 90, 80]).validate()])
    received = []
    assert wait(qt_app, lambda: (window.main_plot.inspect(received.append), received and received[-1]
                                 and received[-1]["height"] > 0)[1], delay=50)
    web = received[-1]
    # The previous layout left 253-258 px for the graph at this size.
    assert web["height"] >= 420, web
    assert web["schema"] == 2 and web["legend"] >= 2
    assert not window.advanced_controls.isVisible() and window.rail.isVisible()
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
    assert restored.appearance == {"mode": "light", "accent": "violet"}
    restored.set_appearance(mode="system")
    expected = "dark" if restored.system_is_dark() else "light"
    assert restored.main_plot.theme["mode"] == expected
    restored.close()


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
    assert window.firmware_hint.isVisibleTo(window) and not window.firmware_body.isVisibleTo(window)
    window.set_firmware(synthetic_firmware(monkeypatch, tmp_path))
    assert window.fit_action.isEnabled() and not window.export_action.isEnabled()
    assert window.firmware_body.isVisibleTo(window) and window.reference_block.isVisibleTo(window)
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
