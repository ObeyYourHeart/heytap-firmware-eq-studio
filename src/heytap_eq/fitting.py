"""Local measurement-to-target correction; independent of firmware adapters and Qt."""

import threading
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares

from heytap_eq.dsp import SAMPLE_RATES, correction, filter_response
from heytap_eq.eq_formats import EQDocument, Filter


@dataclass
class FitOptions:
    low: float = 20
    high: float = 20000
    align_hz: float | None = 1000
    strength: float = 1
    smoothing_octaves: float = 1/12
    max_boost: float = 12
    max_cut: float = 24
    filters: int = 8
    sample_rates: tuple = SAMPLE_RATES

    def validate(self):
        numbers = [self.low, self.high, self.strength, self.smoothing_octaves,
                   self.max_boost, self.max_cut]
        if not all(np.isfinite(x) for x in numbers):
            raise ValueError("Fitting settings must be finite")
        if not (20 <= self.low < self.high <= 20000 and 0 < self.strength <= 1
                and 0 <= self.smoothing_octaves <= 2 and 0 < self.max_boost <= 60
                and 0 < self.max_cut <= 60 and type(self.filters) is int
                and 1 <= self.filters <= 20):
            raise ValueError("Invalid fitting range, strength, bounds or filter count")
        if not self.sample_rates or any(r not in SAMPLE_RATES for r in self.sample_rates):
            raise ValueError("Unsupported fitting sample rate")
        if self.align_hz is not None and not np.isfinite(self.align_hz):
            raise ValueError("Invalid alignment frequency")


def common_grid(original, target, options, points=127):
    original.validate()
    target.validate()
    options.validate()
    low = max(options.low, original.frequencies[0], target.frequencies[0])
    high = min(options.high, original.frequencies[-1], target.frequencies[-1])
    if high <= low:
        raise ValueError("Original and target have no shared fitting frequency range")
    frequency = np.geomspace(low, high, points)
    before = np.interp(np.log(frequency), np.log(original.frequencies), original.spl_values)
    after = np.interp(np.log(frequency), np.log(target.frequencies), target.spl_values)
    offset = 0.0
    if options.align_hz is not None:
        if not low <= options.align_hz <= high:
            raise ValueError("Alignment frequency must be inside the shared fitting range")
        anchor = np.log(options.align_hz)
        offset = (np.interp(anchor, np.log(original.frequencies), original.spl_values)
                  - np.interp(anchor, np.log(target.frequencies), target.spl_values))
    desired = (after + offset - before) * options.strength
    if options.smoothing_octaves:
        sigma = options.smoothing_octaves / np.log2(high/low) * (points-1)
        desired = gaussian_filter1d(desired, sigma, mode="nearest")
    desired = np.clip(desired, -options.max_cut, options.max_boost)
    return frequency, desired, float(offset)


def peak_chain_response(params, frequency, rates):
    """dB response of a PEAK chain at every rate, shape (rates, frequencies).

    params is the optimiser vector [ln fc, gain, ln Q] per filter. The biquad is the
    firmware PEAK (type 1) formula from firmware_dsp.coefficients and the product matches
    dsp.filter_response; it is evaluated for all filters and rates at once because the
    optimiser calls it thousands of times per fit.
    """
    fc, gain, q = np.exp(params[0::3]), params[1::3], np.exp(params[2::3])
    fs = np.asarray(rates, dtype=float)[:, None]
    w = 2 * np.pi * fc / fs
    c, alpha = np.cos(w), np.sin(w) / (2 * q)
    A = 10 ** (gain / 40)
    a0 = 1 + alpha / A
    b0, b1, b2 = ((1 + alpha * A) / a0)[..., None], (-2 * c / a0)[..., None], ((1 - alpha * A) / a0)[..., None]
    a1, a2 = b1, ((1 - alpha / A) / a0)[..., None]
    z = np.exp(-2j*np.pi*np.asarray(frequency)/fs)[:, None, :]
    h = np.prod((b0+b1*z+b2*z*z)/(1+a1*z+a2*z*z), axis=1)
    return 20*np.log10(np.maximum(abs(h), 1e-30))


def fit_response(original, target, mode="RAW", options=None, cancelled=None):
    options = options or FitOptions()
    cancelled = cancelled or threading.Event()
    frequency, desired, offset = common_grid(original, target, options)

    def check_cancel():
        if cancelled.is_set():
            raise InterruptedError("Fitting cancelled")

    check_cancel()
    doc = EQDocument(name=f"{original.title} → {target.title}")
    if mode == "RAW":
        # Bound the interpolated correction with neutral points outside shared data.
        doc.raw = [[float(f), float(g)] for f, g in zip(frequency, desired)]
        if frequency[0] > 20:
            doc.raw.insert(0, [20., 0.])
            doc.raw.insert(1, [float(frequency[0]*.999), 0.])
        if frequency[-1] < 20000:
            doc.raw.extend([[float(min(frequency[-1]*1.001, 20000)), 0.], [20000., 0.]])
            if doc.raw[-1][0] == doc.raw[-2][0]:
                doc.raw.pop()
    elif mode == "PEQ":
        residual = desired.copy()
        for i in range(options.filters):
            check_cancel()
            index = int(np.argmax(np.abs(residual)))
            if abs(residual[index]) < .15:
                break
            doc.filters.append(Filter(i+1, float(frequency[index]),
                                      float(np.clip(residual[index], -options.max_cut,
                                                    options.max_boost)), 1.0))
            initial = np.array([[np.log(f.frequency), f.gain, np.log(f.q)]
                                for f in doc.filters]).ravel()
            low = np.tile([np.log(20), -options.max_cut, np.log(.2)], len(doc.filters))
            high = np.tile([np.log(20000), options.max_boost, np.log(12)], len(doc.filters))

            def filters(params):
                return [Filter(j+1, float(np.exp(fc)), float(gain), float(np.exp(q)))
                        for j, (fc, gain, q) in enumerate(params.reshape(-1, 3))]

            def objective(params):
                check_cancel()
                return (peak_chain_response(params, frequency, options.sample_rates)-desired).ravel()

            result = least_squares(objective, initial, bounds=(low, high), max_nfev=70)
            doc.filters = filters(result.x)
            residual = desired-filter_response(doc.filters, frequency, options.sample_rates[0])
    else:
        raise ValueError("Expected RAW or PEQ fitting mode")
    doc.metadata = {"FIT_MODE": mode, "BASELINE": original.title.replace("\n", " "),
                    "TARGET": target.title.replace("\n", " "),
                    "FIT_LOW_HZ": str(float(frequency[0])), "FIT_HIGH_HZ": str(float(frequency[-1])),
                    "TARGET_OFFSET_DB": str(offset)}
    doc.validate()
    metrics = {}
    for rate in options.sample_rates:
        error = correction(doc, frequency, rate)-desired
        metrics[str(rate)] = {"rms_db": float(np.sqrt(np.mean(error**2))),
                              "max_db": float(np.max(abs(error)))}
    return doc, {"mode": mode, "target_offset_db": offset, "range_hz":
                 [float(frequency[0]), float(frequency[-1])], "rates": metrics,
                 "bounded_smoothed_target": True, "firmware_fit": False}
