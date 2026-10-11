"""Desktop entry and a bounded startup probe shared by source and frozen builds."""

import argparse
import json
import os
import sys
from pathlib import Path

from heytap_eq.runtime import is_packaged, packager


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke-test", action="store_true")
    mode.add_argument("--benchmark", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if (args.smoke_test or args.benchmark) and args.report is None:
        parser.error("--smoke-test and --benchmark require --report")
    if args.benchmark:
        from heytap_eq.benchmark import run_benchmark

        return run_benchmark(args.report)
    from PySide6 import QtCore, QtWidgets

    from heytap_eq.gui import MainWindow
    from heytap_eq.web_plot import initialize_web_engine

    initialize_web_engine()
    app = QtWidgets.QApplication([sys.argv[0]])
    app.setApplicationName("HeyTap Firmware EQ Studio")
    app.setOrganizationName("HeyTapEQStudio")
    auto_path = args.report.parent/"smoke-project.json" if args.smoke_test else None
    window = MainWindow(recover=not args.smoke_test, auto_path=auto_path)
    app.aboutToQuit.connect(window.deleteLater)
    window.show()
    result = 0
    if args.smoke_test:
        from heytap_eq.eq_formats import Filter, parse_text
        from heytap_eq.estimation import builtin_reference
        from heytap_eq.measurements import Measurement

        preview = parse_text("GraphicEQ: 20 0; 100 -1; 1000 2; 5000 -2; 20000 0", "Synthetic preview")
        preview.filters.append(Filter(1, 1000, 2, .7))
        window.set_document(preview)
        window.set_measurements([Measurement("Synthetic test fixture", [20, 1000, 20000], [80, 90, 80]).validate()])

        window.set_targets([Measurement("Synthetic target", [20, 1000, 20000], [83, 90, 78]).validate()])
        window.tabs.setCurrentIndex(0)

        attempts = 0

        def probe():
            nonlocal result
            nonlocal attempts
            attempts += 1
            if not window.digital_plot.is_ready or not window.acoustic_plot.is_ready:
                if attempts < 200:
                    QtCore.QTimer.singleShot(100, probe)
                    return
                result = 1
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps({"status": "failed", "error": "Web editor did not initialize"}))
                app.quit()
                return
            window.acoustic_plot.inspect(finish_probe)

        def finish_probe(web):
            nonlocal result
            try:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                image = args.report.with_suffix(".png")
                assert window.isVisible() and window.width() >= 600
                assert window.grab().save(str(image))
                assert web and web["ready"] and web["svgPaths"] >= 3, web
                assert web["height"] >= 250, web
                reference = builtin_reference()
                assert reference and len(reference["presets"]) == 6
                assert not any(k.startswith(("PyQt5", "PyQt6", "PySide2")) for k in sys.modules)
                args.report.write_text(json.dumps({
                    "status": "passed", "frozen": is_packaged(), "packager": packager(),
                    "qt": QtCore.qVersion(), "qt_binding": "PySide6",
                    "sha": os.environ.get("HEYTAP_BUILD_SHA"), "window_visible": True,
                    "screenshot": image.name, "raw_points": len(window.session.document.raw),
                    "peq_filters": len(window.session.document.filters),
                    "measurements": len(window.measurements), "targets": len(window.targets),
                    "apk_dependency": False,
                    "service_configured": __import__("heytap_eq.service_profile", fromlist=["builtin_authorization"]).builtin_authorization() is not None,
                    "firmware_export_available": callable(getattr(window, "export_to_path", None)),
                    "metadata_editor_available": callable(getattr(window, "set_metadata_edits", None)),
                    "project_schema": "heytap-project-v4",
                    "single_plot": window.digital_plot is window.acoustic_plot,
                    "reference_template_presets": len(reference["presets"]),
                    "plot_engine": "DSSSP 0.8.0", "web_editor": web,
                }, indent=2), encoding="utf-8")
            except Exception as exc:
                result = 1
                args.report.write_text(json.dumps({"status": "failed", "error": str(exc)}))
            finally:
                app.quit()

        QtCore.QTimer.singleShot(1000, probe)
    else:
        QtCore.QTimer.singleShot(0, window.connect_flowmix)
    app.exec()
    # Flush window/page destruction while QApplication is still alive.
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
