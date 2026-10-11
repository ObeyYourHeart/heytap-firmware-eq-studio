"""Numeric measurement imports; HAR request headers never enter the result."""

import base64
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Measurement:
    title: str
    frequencies: list[float]
    spl_values: list[float]
    source: str = "Offline file"
    date: str | None = None
    firmware_version: str | None = None
    measurement_id: str | None = None
    content_version: str | int | None = None

    def validate(self):
        if len(self.frequencies) != len(self.spl_values) or len(self.frequencies) < 2:
            raise ValueError("Measurement arrays must have equal lengths and at least two points")
        previous = 0
        for f, spl in zip(self.frequencies, self.spl_values):
            if not (math.isfinite(f) and math.isfinite(spl) and previous < f):
                raise ValueError("Measurement frequencies must strictly increase; values must be finite")
            previous = f
        return self


def parse_json(data):
    if isinstance(data, dict) and "success" in data:
        if data["success"] is not True:
            raise ValueError("Measurement response did not report success")
        data = data["data"]
    if not isinstance(data, dict):
        raise ValueError("Measurement object expected")
    conditions = data.get("frequencyData")
    if conditions is None:
        conditions = {data.get("title", "Measurement"): data}
    if not isinstance(conditions, dict) or not conditions:
        raise ValueError("No measurement conditions")
    results = []
    for name, condition in conditions.items():
        results.append(Measurement(
            str(condition.get("title", name)), [float(v) for v in condition["frequencies"]],
            [float(v) for v in condition["spl_values"]], str(data.get("sourceName", "Offline file")),
            data.get("lastUpdated"), data.get("firmware_version"),
            condition.get("measurement_id"), condition.get("content_version"),
        ).validate())
    return results


def parse_har(data):
    results = []
    for entry in data["log"]["entries"]:
        content = entry.get("response", {}).get("content", {})
        if "html" not in content.get("mimeType", "").lower():
            continue
        text = content.get("text", "")
        if content.get("encoding") == "base64":
            text = base64.b64decode(text).decode("utf-8")
        match = re.search(r"window\.__INITIAL_DATA__\s*=\s*", text)
        if not match:
            continue
        payload, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
        page = payload.get("Data", {})
        for curve in page.get("data", []):
            title = curve.get("title", "")
            if "B&K5128" not in title.replace(" ", "") or "THD" in title:
                continue
            points = []
            for row in curve["data"]:
                try:
                    f, spl = float(row[0]), float(row[1])
                except (ValueError, TypeError, IndexError):
                    continue
                if f > 0:
                    points.append((f, spl))
            if not points:
                continue
            results.append(Measurement(title, [p[0] for p in points], [p[1] for p in points],
                                       "ReaLab · B&K 5128", str(page.get("time", ""))).validate())
    if not results:
        raise ValueError("No recognized ReaLab frequency response in HAR")
    return results


FREQUENCY_COLUMNS = ("frequency_hz", "frequency", "frequencies", "freq", "hz")
# AutoEQ measurement and target CSVs name the response column "raw".
SPL_COLUMNS = ("spl_db", "spl", "spl_values", "raw", "db", "magnitude")


def column_name(value):
    """Header names without units, so REW "Freq(Hz)" and "SPL(dB)" match."""
    value = re.sub(r"[(\[][^)\]]*[)\]]", "", value).strip().casefold()
    return re.sub(r"\s+", "_", value)


def parse_table(text, title):
    """CSV / REW / squig.link / FRD numeric text: a header row is optional."""
    rows = []
    delimiter = columns = None
    for number, original in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = original.strip()
        if not line or line.startswith(("*", "#", ";", "//")):
            continue  # REW and FRD prefix descriptions with "*".
        if delimiter is None:
            delimiter = next((d for d in (";", "\t", ",") if d in line), " ")
        fields = (next(csv.reader([line], delimiter=delimiter)) if delimiter != " "
                  else line.split())
        fields = [f.strip() for f in fields]
        if delimiter != ",":
            fields = [f.replace(",", ".") for f in fields]  # Decimal commas are unambiguous here.
        if columns is None:
            try:
                [float(f) for f in fields[:2]]
            except ValueError:
                if delimiter == " ":  # Keep "Frequency [Hz]" as one whitespace-separated name.
                    fields = re.sub(r"[(\[][^)\]]*[)\]]", " ", line).split()
                names = [column_name(f) for f in fields]
                frequency = next((names.index(x) for x in FREQUENCY_COLUMNS if x in names), None)
                spl = next((names.index(x) for x in SPL_COLUMNS if x in names), None)
                if frequency is None or spl is None:
                    raise ValueError("Measurement needs frequency and SPL columns "
                                     "(frequency_hz/spl_db, AutoEQ frequency/raw) or two numbers")
                columns = (frequency, spl)
                continue
            columns = (0, 1)
        try:
            rows.append((float(fields[columns[0]]), float(fields[columns[1]])))
        except (ValueError, IndexError):
            raise ValueError(f"Malformed measurement row {number}: {line[:80]}") from None
    if not rows:
        raise ValueError("No measurement rows found")
    return Measurement(title, [r[0] for r in rows], [r[1] for r in rows]).validate()


def load_measurements(path):
    path = Path(path)
    if path.suffix.lower() in (".csv", ".txt", ".frd"):
        return [parse_table(path.read_text(encoding="utf-8-sig"), path.stem)]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return parse_har(data) if path.suffix.lower() == ".har" else parse_json(data)
