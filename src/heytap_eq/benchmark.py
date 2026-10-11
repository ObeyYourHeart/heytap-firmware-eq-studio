"""Offline, deterministic workloads run inside each shipped executable."""

import json
import os
import platform
import time
import traceback
from pathlib import Path

import numpy as np
import scipy

from heytap_eq.dsp import correction
from heytap_eq.eq_formats import EQDocument, Filter, dump_eq, parse_text
from heytap_eq.estimation import builtin_reference
from heytap_eq.firmware_edit import fit_record
from heytap_eq.fitting import FitOptions, fit_response
from heytap_eq.measurements import Measurement
from heytap_eq.runtime import is_packaged, packager

WORKLOAD = "desktop-offline-v1"


def workloads():
    frequency = np.geomspace(20, 20000, 127)
    bell = 4*np.exp(-.5*(np.log2(frequency/1000)/.6)**2)
    doc = EQDocument(raw=[[float(f), float(g)] for f, g in zip(frequency, bell)],
                     filters=[Filter(1, 250, 1.2, 1.5), Filter(2, 1800, -2, 1.3)])
    encoded = dump_eq(doc)
    durations, outputs = {}, {}

    started = time.perf_counter()
    for _ in range(1000):
        restored = parse_text(encoded)
        dump_eq(restored)
    durations["eq_text_roundtrip_1000"] = time.perf_counter()-started
    assert np.allclose(restored.raw, doc.raw, atol=1e-4, rtol=0)
    assert len(restored.filters) == 2
    outputs["eq_points"] = len(restored.raw)

    grid = np.geomspace(20, 20000, 1024)
    started = time.perf_counter()
    for _ in range(100):
        for rate in (44100, 48000, 96000):
            values = correction(doc, grid, rate)
            assert np.all(np.isfinite(values))
    durations["response_300"] = time.perf_counter()-started
    outputs["response_db"] = values[::32].tolist()

    original = Measurement("Synthetic flat", frequency.tolist(), [80.]*127)
    target = Measurement("Synthetic bell", frequency.tolist(), (80+bell).tolist())
    options = FitOptions(align_hz=None, smoothing_octaves=0, filters=2, sample_rates=(48000,))
    started = time.perf_counter()
    for _ in range(4):
        peq, quality = fit_response(original, target, "PEQ", options)
    durations["measurement_peq_fit_4"] = time.perf_counter()-started
    assert quality["rates"]["48000"]["rms_db"] < .3
    outputs["measurement_fit_rms_db"] = quality["rates"]["48000"]["rms_db"]
    outputs["measurement_fit_db"] = correction(peq, frequency)[::4].tolist()

    # Exercise the numerical RAW-to-firmware optimizer, not the exact PEQ cascade shortcut.
    source = {"gain0": -2., "gain1": -2., "count": 3, "slots": [
        {"type_id": 4, "gain": 0., "fc": 30., "q": .707},
        {"type_id": 1, "gain": 1., "fc": 1000., "q": 1.},
        {"type_id": 3, "gain": 0., "fc": 18000., "q": .707},
    ]}
    peak = EQDocument(filters=[Filter(1, 1000., 1., 1.)])
    raw = EQDocument(raw=[[float(f), float(g)] for f, g in
                         zip(frequency, correction(peak, frequency))])
    started = time.perf_counter()
    fitted = fit_record(source, raw, max_nfev=35)
    durations["raw_firmware_fit"] = time.perf_counter()-started
    assert fitted["optimizer_nfev"] > 0
    assert max(m["rms_db"] for m in fitted["metrics"].values()) < .1
    outputs["firmware_fit_rms_db"] = {r: m["rms_db"] for r, m in fitted["metrics"].items()}
    outputs["firmware_fit_nfev"] = fitted["optimizer_nfev"]
    reference = builtin_reference()
    assert reference and len(reference["presets"]) == 6
    outputs["reference_presets"] = len(reference["presets"])
    return {"workload": WORKLOAD, "seconds": durations, "outputs": outputs}


def run_benchmark(report):
    report = Path(report)
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = {"status": "passed", "sha": os.environ.get("HEYTAP_BUILD_SHA"),
                "packager": packager(), "frozen": is_packaged(),
                "python": platform.python_version(), "numpy": np.__version__,
                "scipy": scipy.__version__, "machine": platform.machine(),
                "processor": platform.processor(), "platform": platform.platform(),
                **workloads()}
    except Exception:
        report.write_text(json.dumps({"status": "failed", "error": traceback.format_exc()},
                                     indent=2), encoding="utf-8")
        return 1
    report.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return 0
