"""Build the same portable directory using either supported compiler/packager."""

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("packager", choices=("pyinstaller", "nuitka"))
    args = parser.parse_args()
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    if args.packager == "pyinstaller":
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                   "--windowed", "--name", "HeyTapFirmwareEQStudio", "--paths", "src",
                   "--collect-data", "heytap_eq", "--exclude-module", "PyQt5",
                   "--exclude-module", "PyQt6", "--exclude-module", "PySide2",
                   "scripts/desktop_entry.py"]
    else:
        command = [sys.executable, "-m", "nuitka", "--mode=standalone", "--enable-plugin=pyside6",
                   "--msvc=latest", "--jobs=4", "--lto=no", "--assume-yes-for-downloads",
                   "--windows-console-mode=attach", "--output-dir=dist",
                   "--output-filename=HeyTapFirmwareEQStudio.exe",
                   "--include-package=heytap_eq", "--include-package-data=heytap_eq",
                   "--report=reports/nuitka-compilation.xml", "scripts/desktop_entry.py"]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    elapsed = time.perf_counter()-started
    folder = Path("dist/HeyTapFirmwareEQStudio")
    if args.packager == "nuitka":
        candidates = list(Path("dist").glob("*.dist/HeyTapFirmwareEQStudio.exe"))
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one standalone executable, found {candidates}")
        candidates[0].parent.rename(folder)
    exe = folder/"HeyTapFirmwareEQStudio.exe"
    assert exe.is_file()
    (folder/"SOURCE_SHA.txt").write_text(os.environ["HEYTAP_BUILD_SHA"]+"\n", encoding="utf-8")
    (folder/"PACKAGER.txt").write_text(args.packager+"\n", encoding="utf-8")
    files = [p for p in folder.rglob("*") if p.is_file()]
    (reports/"packaging.json").write_text(json.dumps({
        "sha": os.environ["HEYTAP_BUILD_SHA"], "packager": args.packager,
        "packager_version": importlib.metadata.version(args.packager),
        "build_seconds": elapsed, "portable_bytes": sum(p.stat().st_size for p in files),
        "executable_bytes": exe.stat().st_size, "files": len(files),
        "mode": "standalone-directory", "static_qt_webengine": False,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
