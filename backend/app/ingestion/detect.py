"""Format detection from the file name plus the first 64 KB."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath

HEAD_BYTES = 64 * 1024


class Format(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    NDJSON = "ndjson"
    XLSX = "xlsx"
    JUNIT_XML = "junit_xml"
    JTL_CSV = "jtl_csv"
    JTL_XML = "jtl_xml"
    UNKNOWN = "unknown"


SUPPORTED = "CSV/TSV, JSON, NDJSON, Excel (.xlsx), JUnit/xUnit XML and JMeter JTL (CSV or XML)"


@dataclass(frozen=True, slots=True)
class Detection:
    format: Format
    confidence: float
    reason: str
    delimiter: str | None = None


_XML_ROOT = re.compile(r"<(?![?!])([A-Za-z_][\w.:-]*)")
_JTL_CSV_COLUMNS = {"timestamp", "elapsed", "label"}
_DELIMITERS = (",", ";", "\t", "|")


def _xml_root(text: str) -> str | None:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    m = _XML_ROOT.search(text)
    return m.group(1).split(":")[-1] if m else None


def _delimited(lines: list[str]) -> tuple[str, float] | None:
    """Best delimiter and the share of lines agreeing on its column count."""
    best: tuple[str, float] | None = None
    sample = [ln for ln in lines[:50] if ln.strip()]
    if len(sample) < 1:
        return None
    for d in _DELIMITERS:
        counts = [ln.count(d) for ln in sample]
        if counts[0] == 0:
            continue
        agree = sum(1 for c in counts if c == counts[0]) / len(counts)
        if (
            best is None
            or agree > best[1]
            or (agree == best[1] and counts[0] > sample[0].count(best[0]))
        ):
            best = (d, agree)
    return best


def detect(filename: str, head: bytes) -> Detection:
    """Return the most likely format with a confidence in [0, 1]."""
    ext = PurePath(filename).suffix.lower()

    if head.startswith(b"PK\x03\x04"):
        conf = 0.99 if ext in {".xlsx", ".xlsm"} else 0.7
        return Detection(Format.XLSX, conf, "zip container (Excel workbook)")

    text = head.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if not stripped:
        return Detection(Format.UNKNOWN, 0.0, "The file is empty.")

    if stripped.startswith("<"):
        root = _xml_root(stripped)
        if root in {"testsuites", "testsuite"}:
            return Detection(Format.JUNIT_XML, 0.98, f"XML root <{root}>")
        if root == "testResults":
            return Detection(Format.JTL_XML, 0.98, "XML root <testResults>")
        return Detection(
            Format.UNKNOWN,
            0.3,
            f"XML with root <{root}> isn't a supported format. Supported: {SUPPORTED}.",
        )

    if stripped[0] in "[{":
        lines = [ln for ln in stripped.splitlines() if ln.strip()]
        objects = 0
        for ln in lines[:5]:
            try:
                if isinstance(json.loads(ln), dict):
                    objects += 1
            except ValueError:
                break
        if objects >= 2 or (objects == 1 and len(lines) == 1 and ext in {".ndjson", ".jsonl"}):
            return Detection(Format.NDJSON, 0.97, "one JSON object per line")
        if stripped[0] == "[" or ext == ".json":
            return Detection(Format.JSON, 0.95 if stripped[0] == "[" else 0.9, "JSON document")
        if ext in {".ndjson", ".jsonl"}:
            return Detection(Format.NDJSON, 0.9, "NDJSON by extension")
        return Detection(Format.JSON, 0.9, "JSON document")

    lines = stripped.splitlines()
    header = {h.strip().strip('"').lower() for h in re.split(r"[,;\t|]", lines[0])}
    if header >= _JTL_CSV_COLUMNS:
        delim = _delimited(lines)
        return Detection(
            Format.JTL_CSV, 0.98, "JMeter CSV header", delimiter=delim[0] if delim else ","
        )

    delim = _delimited(lines)
    if delim is not None:
        d, agree = delim
        fmt = Format.TSV if d == "\t" else Format.CSV
        by_ext = ext in {".csv", ".tsv", ".txt"}
        header_fields = lines[0].count(d) + 1
        conf = 0.95 if agree >= 0.9 else 0.92 if by_ext and header_fields >= 2 else 0.5
        if conf >= 0.75 or by_ext:
            return Detection(fmt, conf, f"delimited text ({d!r})", delimiter=d)

    if ext in {".csv", ".tsv"}:
        return Detection(
            Format.CSV if ext == ".csv" else Format.TSV,
            0.6,
            "CSV by extension",
            delimiter="\t" if ext == ".tsv" else ",",
        )
    # TODO(post-mvp): learned parsers for unstructured text logs (Drain3 + LLM).
    return Detection(
        Format.UNKNOWN,
        0.0,
        f"Pheme can't read this kind of file yet. Supported formats: {SUPPORTED}.",
    )
