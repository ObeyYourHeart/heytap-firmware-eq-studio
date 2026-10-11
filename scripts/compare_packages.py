"""Interleave real executable workloads on one runner; no app dependencies needed."""

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path


def matching_outputs(before, after):
    if isinstance(before, dict):
        assert before.keys() == after.keys()
        for key in before:
            matching_outputs(before[key], after[key])
    elif isinstance(before, list):
        assert len(before) == len(after)
        for left, right in zip(before, after):
            matching_outputs(left, right)
    elif isinstance(before, (float, int)):
        assert math.isclose(before, after, rel_tol=1e-5, abs_tol=1e-5), (before, after)
    else:
        assert before == after


def run(exe, name, folder, index):
    folder.mkdir(parents=True, exist_ok=True)
    result = {}
    child_env = os.environ.copy()
    child_env.pop("PYTHONFAULTHANDLER", None)
    for flag, label in (("--smoke-test", "startup"), ("--benchmark", "benchmark")):
        report = (folder/f"{label}-{index}.json").resolve()
        report.unlink(missing_ok=True)
        started = time.perf_counter()
        process = subprocess.run([str(exe), flag, "--report", str(report)], timeout=180,
                                 capture_output=True, text=True, errors="replace", env=child_env)
        if process.returncode:
            print(process.stdout)
            print(process.stderr)
            if report.is_file():
                print(report.read_text(encoding="utf-8"))
            process.check_returncode()
        elapsed = time.perf_counter()-started
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["status"] == "passed" and data["frozen"] and data["packager"] == name
        assert data["sha"] == os.environ["HEYTAP_BUILD_SHA"]
        if label == "startup":
            assert data["window_visible"] and data["web_editor"]["ready"]
            assert data["single_plot"] and data["web_editor"]["svgPaths"] >= 3
            assert data["reference_template_presets"] == 6 and data["service_configured"]
            result["gui_smoke_startup"] = elapsed
        else:
            assert data["workload"] == "desktop-offline-v1"
            result.update(data["seconds"])
            result["benchmark_process"] = elapsed
            result["outputs"] = data["outputs"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", type=Path, default=Path("packages"))
    parser.add_argument("--reports", type=Path, default=Path("reports/comparison"))
    args = parser.parse_args()
    names = ("pyinstaller", "nuitka")
    exes = {name: (args.packages/name/"HeyTapFirmwareEQStudio"/
                   "HeyTapFirmwareEQStudio.exe").resolve() for name in names}
    assert all(p.is_file() for p in exes.values())
    # Full warmup for both packages is discarded; subsequent order alternates.
    for name in names:
        run(exes[name], name, args.reports/name, "warmup")
    samples = {name: [] for name in names}
    for index in range(3):
        for name in (names if index % 2 == 0 else names[::-1]):
            samples[name].append(run(exes[name], name, args.reports/name, index))
        matching_outputs(samples[names[0]][-1]["outputs"], samples[names[1]][-1]["outputs"])
    timings = {}
    for key in samples[names[0]][0]:
        if key == "outputs":
            continue
        p = statistics.median(s[key] for s in samples["pyinstaller"])
        n = statistics.median(s[key] for s in samples["nuitka"])
        timings[key] = {"pyinstaller_median_seconds": p, "nuitka_median_seconds": n,
                        "nuitka_speedup": p/n}
    result = {"status": "passed", "sha": os.environ["HEYTAP_BUILD_SHA"],
              "workload": "desktop-offline-v1", "same_runner": True, "warmups": 1,
              "measured_runs": 3, "matching_numeric_outputs": True,
              "platform": platform.platform(), "processor": platform.processor(),
              "native_threads": 1, "timings": timings, "samples": samples,
              "executable_sha256": {n: hashlib.sha256(p.read_bytes()).hexdigest()
                                    for n, p in exes.items()},
              "startup_note": "GUI probe includes its deliberate 1-second readiness timer; "
                              "warm-cache end-to-end probe time, not time to first frame.",
              "scope": "Synthetic offline EQ parsing, frequency response, measurement PEQ "
                       "fit and RAW firmware optimizer; not chart FPS or on-device DSP."}
    args.reports.mkdir(parents=True, exist_ok=True)
    (args.reports/"comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "timings": timings}, indent=2))


if __name__ == "__main__":
    main()
