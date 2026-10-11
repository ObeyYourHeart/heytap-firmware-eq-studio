import threading

import numpy as np
import pytest

from heytap_eq.fitting import FitOptions, fit_response
from heytap_eq.measurements import Measurement
from heytap_eq.preferences import BrowserMemory, unique_match
from heytap_eq.session import Session


def test_raw_aligns_different_frequency_grids_without_extrapolation():
    original = Measurement("A", [20, 1000, 20000], [85, 90, 88])
    target = Measurement("B", [40, 200, 1000, 18000], [2, 3, 0, -2])
    doc, report = fit_response(original, target, options=FitOptions(smoothing_octaves=0))
    assert report["range_hz"] == [40, 18000]
    assert report["target_offset_db"] == 90
    assert doc.raw[0] == [20, 0] and doc.raw[-1] == [20000, 0]
    assert all(v["rms_db"] < 1e-9 for v in report["rates"].values())
    with pytest.raises(ValueError, match="shared"):
        fit_response(original, Measurement("No overlap", [30000, 40000], [1, 2]))


def test_peq_recovers_a_smooth_peak_and_cancel_does_not_return_a_document():
    frequency = np.geomspace(20, 20000, 127)
    # An independently specified smooth bell, rather than an optimizer response fixture.
    target_db = 4*np.exp(-.5*(np.log2(frequency/1000)/.6)**2)
    before = Measurement("Flat", frequency.tolist(), [80.]*127)
    after = Measurement("Bell", frequency.tolist(), (80+target_db).tolist())
    options = FitOptions(align_hz=None, smoothing_octaves=0, filters=2, sample_rates=(48000,))
    doc, report = fit_response(before, after, "PEQ", options)
    assert len(doc.filters) <= 2
    assert report["rates"]["48000"]["rms_db"] < .3
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        fit_response(before, after, cancelled=cancel)


def synthetic_pair():
    """A bumpy 'measurement' and a shelf + ear-gain target; synthetic, no vendor data."""
    f = np.geomspace(20, 20000, 400)
    bumps = (3*np.exp(-.5*(np.log2(f/60)/.5)**2) - 4*np.exp(-.5*(np.log2(f/4000)/.3)**2)
             + 5*np.exp(-.5*(np.log2(f/9000)/.25)**2))
    target = 6/(1+(f/105)**2) + 8*np.exp(-.5*(np.log2(f/3000)/.55)**2) - 4/(1+(9000/f)**4)
    return (Measurement("Bumpy", f.tolist(), (90+bumps).tolist()).validate(),
            Measurement("Target", f.tolist(), target.tolist()).validate())


def reference_chain(params, frequency, rates):
    """The previous objective: one validated Filter and one biquad call per filter and rate."""
    from heytap_eq.dsp import filter_response
    from heytap_eq.eq_formats import Filter

    chain = [Filter(i+1, float(np.exp(fc)), float(gain), float(np.exp(q)))
             for i, (fc, gain, q) in enumerate(params.reshape(-1, 3))]
    return np.array([filter_response(chain, frequency, rate) for rate in rates])


def test_vectorised_peak_chain_matches_the_firmware_biquads():
    from heytap_eq.dsp import SAMPLE_RATES
    from heytap_eq.fitting import peak_chain_response

    rng = np.random.default_rng(7)
    frequency = np.geomspace(20, 20000, 127)
    for count in (1, 3, 8, 20):
        # The optimiser bounds: 20 Hz-20 kHz, -24..+12 dB, Q 0.2-12.
        params = np.column_stack([rng.uniform(np.log(20), np.log(20000), count), rng.uniform(-24, 12, count),
                                  rng.uniform(np.log(.2), np.log(12), count)]).ravel()
        np.testing.assert_allclose(peak_chain_response(params, frequency, SAMPLE_RATES),
                                   reference_chain(params, frequency, SAMPLE_RATES), rtol=0, atol=1e-9)


def test_peq_fit_is_unchanged_from_the_per_filter_objective(monkeypatch):
    import heytap_eq.fitting as fitting

    original, target = synthetic_pair()
    options = FitOptions(filters=4)
    fast, fast_report = fit_response(original, target, "PEQ", options)
    monkeypatch.setattr(fitting, "peak_chain_response", reference_chain)
    slow, slow_report = fit_response(original, target, "PEQ", options)
    assert len(fast.filters) == len(slow.filters) == 4
    np.testing.assert_allclose([[f.frequency, f.gain, f.q] for f in fast.filters],
                               [[f.frequency, f.gain, f.q] for f in slow.filters], rtol=1e-7, atol=1e-9)
    for rate, metrics in slow_report["rates"].items():
        assert fast_report["rates"][rate] == pytest.approx(metrics, rel=1e-7, abs=1e-9)


def test_eight_peq_fit_stays_interactive():
    import time

    original, target = synthetic_pair()
    started = time.perf_counter()
    doc, report = fit_response(original, target, "PEQ", FitOptions(filters=8))
    # The per-filter objective needed ~10 s for this on a desktop CPU; now ~0.3 s.
    # The bound is loose for slow CI runners but still catches a return to the old path.
    assert time.perf_counter()-started < 4
    assert len(doc.filters) == 8
    assert max(m["rms_db"] for m in report["rates"].values()) < 1.5


def test_package_import_keeps_blas_single_threaded_unless_the_user_chose():
    import os
    import subprocess
    import sys

    code = "import heytap_eq, os; print(os.environ['OPENBLAS_NUM_THREADS'])"
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    run = lambda: subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,  # noqa: E731
                                 text=True, check=True).stdout.strip()
    assert run() == "1"
    env["OPENBLAS_NUM_THREADS"] = "3"
    assert run() == "3"


def test_browser_memory_is_empty_initially_and_remembers_each_device(tmp_path):
    memory = BrowserMemory(tmp_path/"memory.json")
    assert memory.selection() == {}
    first = {"source": "woodenears", "brand": "A", "headphone": "A_1"}
    memory.remember(first, "device-a")
    memory.remember({"source": "realab", "brand": "B", "headphone": "B_2"}, "device-b")
    restored = BrowserMemory(memory.path)
    assert restored.selection("device-a") == first
    assert restored.selection()["headphone"] == "B_2"
    assert restored.selection("unseen") == {}
    entries = [{"name": "Brand_Model_1", "display": "Brand Model 1"}]
    assert unique_match(entries, ["Brand Model 1"]) == "Brand_Model_1"
    assert unique_match(entries+entries, ["Brand Model 1"]) is None


def test_project_restores_measurements_targets_and_rejects_bad_curves_atomically(tmp_path):
    import json

    session = Session()
    session.measurements = [Measurement("Original", [20, 20000], [80, 90])]
    session.targets = [Measurement("Target", [20, 20000], [0, 1])]
    session.measurement_index = session.target_index = 0
    session.online_selection = {"source": "woodenears"}
    path = tmp_path/"project.json"
    session.save(path)
    restored = Session()
    restored.restore(path)
    assert restored.measurements == session.measurements and restored.targets == session.targets
    payload = json.loads(path.read_text())
    payload["targets"][0]["frequencies"] = [20000, 20]
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        restored.restore(path)
    assert restored.targets == session.targets
