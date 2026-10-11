"""Run the actual packaged Windows executable, requiring its event-loop report."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from heytap_eq.service_profile import builtin_authorization

parser = argparse.ArgumentParser()
parser.add_argument("--packager", choices=("pyinstaller", "nuitka"), required=True)
args = parser.parse_args()
exe = Path("dist/HeyTapFirmwareEQStudio/HeyTapFirmwareEQStudio.exe").resolve()
report = Path("reports/frozen-startup.json").resolve()
report.parent.mkdir(exist_ok=True)
report.unlink(missing_ok=True)
# Windowed interpreters may not have stderr during their early initialization.
child_env = os.environ.copy()
child_env.pop("PYTHONFAULTHANDLER", None)
# Importing heytap_eq above set this in our own process; the executable must set it by itself.
child_env.pop("OPENBLAS_NUM_THREADS", None)
started = time.perf_counter()
process = subprocess.run([str(exe), "--smoke-test", "--report", str(report)],
                         timeout=60, capture_output=True, text=True, errors="replace", env=child_env)
elapsed = time.perf_counter()-started
if process.returncode:
    print(process.stdout)
    print(process.stderr)
    if report.is_file():
        print(report.read_text(encoding="utf-8"))
    process.check_returncode()
data = json.loads(report.read_text())
assert data["status"] == "passed" and data["frozen"] and data["window_visible"]
assert data["packager"] == args.packager
assert data["sha"] == os.environ["HEYTAP_BUILD_SHA"]
assert data["firmware_export_available"] and data["metadata_editor_available"]
assert data["project_schema"] == "heytap-project-v4"
assert data["single_plot"] and data["reference_template_presets"] == 6
assert not data["apk_dependency"]
assert data["plot_engine"] == "DSSSP 0.8.0"
assert data["web_editor"]["ready"] and data["web_editor"]["svgPaths"] >= 3
assert data["web_editor"]["height"] >= 250
assert data["service_configured"] == (builtin_authorization() is not None)
# Themed arrows are SVG files (qsvg plugin + Qt6Svg), dialogs use the zh_CN qtbase catalogue,
# and the package sets single-threaded BLAS before numpy loads (removed from the child above).
assert data["svg_icons"] and data["qt_translation_zh_CN"] and data["blas_threads"] == "1", data
data["executable_sha256"] = hashlib.sha256(exe.read_bytes()).hexdigest()
data["gui_smoke_elapsed_seconds"] = elapsed
report.write_text(json.dumps(data, indent=2))
