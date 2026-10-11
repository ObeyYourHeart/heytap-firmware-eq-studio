"""Strict, lossless numeric import of GraphicEQ and Flowmix RAW plus PEQ."""

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

KINDS = ("PEAK", "LS", "HS", "LP", "HP", "NOTCH", "BAND_PASS", "ALL_PASS")
ALIASES = {"PK": "PEAK", "LOW_SHELF": "LS", "HIGH_SHELF": "HS",
           "LOW_PASS": "LP", "HIGH_PASS": "HP", "AP": "ALL_PASS", "BP": "BAND_PASS"}
SECTIONS = ("[RAW]", "[PEQ]", "[METADATA]")
# Equalizer APO / AutoEQ ParametricEQ / REW "Generic" text. Only the explicit-Q forms are
# accepted: shelves use the RBJ midpoint frequency, as the firmware coefficients do, while
# corner-frequency, slope and bandwidth variants have other semantics and are rejected.
APO_LINE = re.compile(r"(?:preamp|filter\s*\d*)\s*:", re.I)
APO_ROUTING = re.compile(r"(?:(?:channel|device|include|copy|convolution|delay|stage|eval|if|elseif"
                         r"|loudnesscorrection)\s*:|(?:else|endif)\s*$)", re.I)
APO_NUMBER = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)"
APO_FILTER = re.compile(rf"(ON|OFF)\s+([A-Z]+)\s+Fc\s+{APO_NUMBER}\s*Hz"
                        rf"(?:\s+Gain\s+{APO_NUMBER}\s*dB)?(?:\s+Q\s+{APO_NUMBER})?", re.I)
APO_KINDS = {"PK": "PEAK", "PEQ": "PEAK", "MODAL": "PEAK", "LS": "LS", "LSC": "LS",
             "HS": "HS", "HSC": "HS", "LP": "LP", "LPQ": "LP", "HP": "HP", "HPQ": "HP",
             "BP": "BAND_PASS", "NO": "NOTCH", "AP": "ALL_PASS"}


@dataclass
class Filter:
    id: int
    frequency: float
    gain: float
    q: float
    kind: str = "PEAK"
    enabled: bool = True

    def validate(self):
        if type(self.id) is not int or self.id < 0 or type(self.enabled) is not bool:
            raise ValueError("Invalid PEQ ID or enabled flag")
        if self.kind not in KINDS:
            raise ValueError(f"Unsupported PEQ type: {self.kind}")
        if not all(math.isfinite(x) for x in (self.frequency, self.gain, self.q)):
            raise ValueError("PEQ values must be finite")
        if not (0 < self.frequency < 22050 and abs(self.gain) <= 60 and .01 <= self.q <= 100):
            raise ValueError("PEQ frequency / gain / Q outside supported preview range")


@dataclass
class EQDocument:
    name: str = "Untitled"
    raw: list[list[float]] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    source_sha256: str | None = None

    def validate(self):
        if not isinstance(self.name, str) or not isinstance(self.metadata, dict):
            raise ValueError("Invalid EQ name or metadata")
        if any(not isinstance(k, str) or not re.fullmatch(r"[A-Z_0-9]+", k)
               or not isinstance(v, str) or "\n" in v or "\r" in v
               for k, v in self.metadata.items()):
            raise ValueError("Invalid EQ metadata fields")
        if len(self.raw) == 1:
            raise ValueError("GraphicEQ needs at least two points")
        previous = 0
        for point in self.raw:
            if len(point) != 2 or not all(math.isfinite(x) for x in point):
                raise ValueError("Invalid GraphicEQ point")
            frequency, gain = point
            if not (previous < frequency < 22050 and abs(gain) <= 60):
                raise ValueError("GraphicEQ frequencies must strictly increase; gains within ±60 dB")
            previous = frequency
        seen = set()
        for f in self.filters:
            f.validate()
            if f.id in seen:
                raise ValueError(f"Duplicate PEQ ID: {f.id}")
            seen.add(f.id)

    def to_dict(self):
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        obj = cls(**{**data, "filters": [Filter(**f) for f in data["filters"]]})
        obj.validate()
        return obj


def graphic_points(value):
    points = []
    for pair in value.split(";"):
        if pair.strip():
            fields = pair.split()
            if len(fields) != 2:
                raise ValueError("GraphicEQ point must be frequency and gain")
            points.append([float(x) for x in fields])
    return points


def parse_apo(lines, name):
    """Single-channel Equalizer APO text; IDs follow file order since APO ignores numbers."""
    doc = EQDocument(name=name)
    graphic_seen = started = False
    preamp = None
    for line in lines:
        if APO_ROUTING.match(line):
            raise ValueError(f"Unsupported Equalizer APO command: {line[:80]}")
        if not started and not APO_LINE.match(line) and not line.lower().startswith("graphiceq:"):
            continue  # REW filter exports start with a free-form description.
        started = True
        key, _, value = (s.strip() for s in line.partition(":"))
        if key.upper() == "GRAPHICEQ":
            if graphic_seen:
                raise ValueError("Duplicate GraphicEQ line")
            graphic_seen = True
            doc.raw = graphic_points(value)
        elif key.upper() == "PREAMP":
            match = re.fullmatch(APO_NUMBER+r"\s*dB", value, re.I)
            if not match:
                raise ValueError(f"Malformed Equalizer APO preamp: {value[:80]}")
            preamp = (preamp or 0.)+float(match[1])
        elif APO_LINE.match(line):
            if re.fullmatch(r"(?:ON|OFF)\s+NONE", value, re.I):
                continue  # REW lists unused filter slots as "OFF None".
            match = APO_FILTER.fullmatch(value)
            kind = APO_KINDS.get(match[2].upper()) if match else None
            if kind is None:
                raise ValueError(f"Unsupported Equalizer APO filter: {value[:80]}")
            enabled, _, frequency, gain, q = match.groups()
            if q is None:
                raise ValueError(f"Equalizer APO filter needs an explicit Q: {value[:80]}")
            takes_gain = kind in ("PEAK", "LS", "HS")
            if takes_gain and gain is None:
                raise ValueError(f"Equalizer APO filter needs a gain: {value[:80]}")
            if not takes_gain and float(gain or 0) != 0:
                raise ValueError(f"Equalizer APO {kind} filter does not take a gain: {value[:80]}")
            doc.filters.append(Filter(len(doc.filters)+1, float(frequency), float(gain or 0),
                                      float(q), kind, enabled.upper() == "ON"))
        else:
            raise ValueError(f"Unsupported Equalizer APO line: {line[:80]}")
    if preamp is not None:
        doc.metadata["PREAMP_DB"] = f"{preamp:.9g}"
    return doc


def parse_text(text: str, name="Imported EQ") -> EQDocument:
    lines = [s for s in (x.strip() for x in text.lstrip("\ufeff").splitlines())
             if s and not s.startswith(("#", ";"))]
    if not any(s.upper() in SECTIONS for s in lines) and any(APO_LINE.match(s) for s in lines):
        doc = parse_apo(lines, name)
        if not doc.raw and not doc.filters:
            raise ValueError("No EQ points or filters found")
        doc.validate()
        return doc
    doc = EQDocument(name=name)
    graphic_seen = False
    started = False
    metadata = {}
    for line in lines:
        if not started and not line.startswith("[") and not line.lower().startswith("graphiceq:"):
            continue  # Flowmix exports include a decorative preamble before the first section.
        started = True
        if line.startswith("["):
            if line.upper() not in SECTIONS:
                raise ValueError(f"Unsupported section: {line}")
            continue
        if ":" not in line:
            raise ValueError(f"Malformed EQ line: {line[:80]}")
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.upper()
        if key == "GRAPHICEQ":
            if graphic_seen:
                raise ValueError("Duplicate GraphicEQ line")
            graphic_seen = True
            doc.raw = graphic_points(value)
        elif re.fullmatch(r"PEQ\d+", key):
            fields = value.split()
            if len(fields) not in (3, 4):
                raise ValueError("PEQ requires frequency, gain, Q, and optional type")
            kind = fields[3].upper() if len(fields) == 4 else "PEAK"
            kind = ALIASES.get(kind, kind)
            doc.filters.append(Filter(int(key[3:]), *[float(x) for x in fields[:3]], kind))
        elif key.startswith("PEQ") and key != "PEQ_COUNT":
            raise ValueError("Malformed PEQ key")
        else:
            if key in metadata:
                raise ValueError(f"Duplicate metadata: {key}")
            metadata[key] = value
    if not doc.raw and not doc.filters:
        raise ValueError("No EQ points or filters found")
    for key, count in (("RAW_BANDS", len(doc.raw)), ("PEQ_COUNT", len(doc.filters))):
        if key in metadata and int(metadata[key]) != count:
            raise ValueError(f"{key} does not match actual entries")
    doc.metadata = metadata
    doc.validate()
    return doc


def load_eq(path):
    path = Path(path)
    raw = path.read_bytes()
    doc = parse_text(raw.decode("utf-8-sig"), path.stem)
    doc.source_sha256 = hashlib.sha256(raw).hexdigest()
    return doc


def dump_eq(doc):
    doc.validate()
    if not doc.raw and not doc.filters:
        raise ValueError("No EQ points or filters to export")
    if any(not f.enabled for f in doc.filters):
        raise ValueError("Flowmix text has no confirmed disabled-filter syntax; enable or remove filters")
    graphic = "; ".join(f"{f:.9g} {g:.9g}" for f, g in doc.raw)
    lines = ["[RAW]", "GraphicEQ: " + graphic, "", "[PEQ]"]
    lines += [f"PEQ{f.id}: {f.frequency:.9g} {f.gain:.9g} {f.q:.9g} {f.kind}" for f in doc.filters]
    meta = {**doc.metadata, "RAW_BANDS": str(len(doc.raw)), "PEQ_COUNT": str(len(doc.filters))}
    lines += ["", "[METADATA]"] + [f"{k}: {v}" for k, v in meta.items()]
    return "\n".join(lines) + "\n"
