"""Parsers: file on disk → normalised batches of a canonical kind.

CSV, TSV, JMeter CSV and NDJSON are read in fixed-size chunks (``CHUNK_BYTES``) with the
schema fixed by the first chunk, so memory stays bounded whatever the file size; Polars'
own scan + sink grows with the input. JUnit and JMeter XML use ``lxml.iterparse``. JSON
documents and Excel workbooks are loaded fully: a known limit, noted in the phase plan.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

from app.ingestion.detect import Detection, Format
from app.ingestion.errors import IngestionError, readable
from app.ingestion.normalize import (
    PERF_SAMPLES_SCHEMA,
    TEST_RESULTS_SCHEMA,
    ParseContext,
    build_from_filename,
    normalize_generic,
    parse_timestamp,
    standard_status,
)
from app.models.dataset import DatasetKind

INFER_ROWS = 10_000
CHUNK_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Parsed:
    kind: DatasetKind
    batches: Iterator[pl.DataFrame]  # lazy: read errors surface while writing


# --------------------------------------------------------------------------- chunked readers


def _line_chunks(fh: BinaryIO, header: bytes | None, quoted: bool) -> Iterator[bytes]:
    """Yield ~CHUNK_BYTES of whole lines (plus the header). With ``quoted``, a chunk is
    extended until its quote count is even, so quoted newlines never split a record."""
    while True:
        lines = fh.readlines(CHUNK_BYTES)
        if not lines:
            return
        data = b"".join(lines)
        if quoted:
            while data.count(b'"') % 2:
                nxt = fh.readline()
                if not nxt:
                    break
                data += nxt
        yield (header or b"") + data


def _csv_batches(
    path: Path, separator: str, overrides: dict[str, pl.DataType] | None = None
) -> Iterator[pl.DataFrame]:
    schema: pl.Schema | None = None
    with path.open("rb") as fh:
        header = fh.readline()
        if not header.strip():
            raise IngestionError("The file is empty.")
        for chunk in _line_chunks(fh, header, quoted=True):
            if schema is None:
                df = pl.read_csv(
                    io.BytesIO(chunk), separator=separator, infer_schema_length=INFER_ROWS,
                    schema_overrides=overrides, try_parse_dates=False,
                )  # fmt: skip
                schema = df.schema
            else:
                df = pl.read_csv(io.BytesIO(chunk), separator=separator, schema=schema)
            yield df


def _ndjson_batches(path: Path) -> Iterator[pl.DataFrame]:
    schema: pl.Schema | None = None
    with path.open("rb") as fh:
        for chunk in _line_chunks(fh, None, quoted=False):
            if not chunk.strip():
                continue
            if schema is None:
                df = pl.read_ndjson(io.BytesIO(chunk), infer_schema_length=None)
                schema = df.schema
            else:
                df = pl.read_ndjson(io.BytesIO(chunk), schema=schema)
            yield df


# --------------------------------------------------------------------------- JUnit

_BUILD_PROPS = {
    "build",
    "build_number",
    "buildnumber",
    "build.number",
    "build_id",
    "ci.build",
    "ci_build",
}


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_junit(path: Path, ctx: ParseContext) -> Parsed:
    rows: dict[str, list[Any]] = {k: [] for k in TEST_RESULTS_SCHEMA}
    stack: list[dict[str, Any]] = []
    file_build = build_from_filename(ctx.filename)
    try:
        for event, el in etree.iterparse(str(path), events=("start", "end"), huge_tree=True):
            tag = _local(el.tag)
            if event == "start":
                if tag in {"testsuites", "testsuite"}:
                    stack.append(
                        {"tag": tag, "name": el.get("name"), "ts": el.get("timestamp"), "props": {}}
                    )
                continue
            if tag == "property" and stack:
                name = (el.get("name") or "").strip().lower()
                if name in _BUILD_PROPS and el.get("value"):
                    stack[-1]["props"]["build"] = el.get("value")
            elif tag == "testcase":
                suites = [f for f in stack if f["tag"] == "testsuite"]
                suite = suites[-1]["name"] if suites else el.get("classname")
                status, etype, emsg = "passed", None, None
                for child in el:
                    ctag = _local(child.tag)
                    if ctag in {"failure", "error", "skipped"}:
                        status = {"failure": "failed", "error": "error", "skipped": "skipped"}[ctag]
                        etype = child.get("type") if ctag != "skipped" else None
                        emsg = child.get("message") or (child.text or "").strip()[:500] or None
                        break
                if el.get("status"):
                    status = standard_status(el.get("status"))
                prop_build = next(
                    (f["props"]["build"] for f in reversed(stack) if "build" in f["props"]), None
                )
                raw_ts = next((f["ts"] for f in reversed(stack) if f["ts"]), None)
                ts, tz = parse_timestamp(raw_ts, ctx.default_tz)
                t = el.get("time")
                rows["run_id"].append(ctx.upload_id)
                rows["build"].append(ctx.build or prop_build or file_build or str(ctx.build_order))
                rows["suite"].append(suite)
                rows["case"].append(el.get("name"))
                rows["status"].append(status)
                rows["duration_ms"].append(float(t.replace(",", "")) * 1000 if t else None)
                rows["error_type"].append(etype)
                rows["error_message"].append(emsg)
                rows["timestamp"].append(ts)
                rows["orig_tz"].append(tz)
                el.clear()
            elif tag in {"testsuites", "testsuite"} and stack:
                stack.pop()
                el.clear()
    except etree.XMLSyntaxError as exc:
        raise IngestionError(
            f"The JUnit XML file is malformed (line {exc.lineno}): {exc.msg}."
        ) from None
    except ValueError as exc:
        raise readable("A test case in the JUnit file has an unreadable value", exc) from None
    if not rows["case"]:
        raise IngestionError("The JUnit file has no <testcase> elements.")
    return Parsed(DatasetKind.TEST_RESULTS, iter([pl.DataFrame(rows, schema=TEST_RESULTS_SCHEMA)]))


# --------------------------------------------------------------------------- JMeter


def _jtl_csv_frame(df: pl.DataFrame) -> pl.DataFrame:
    names = set(df.columns)
    ts_dtype = df.schema.get("timeStamp")
    if ts_dtype is not None and ts_dtype.is_integer():
        ts = pl.from_epoch(pl.col("timeStamp"), time_unit="ms").dt.replace_time_zone("UTC")
    else:
        ts = pl.col("timeStamp").cast(pl.String).str.to_datetime(time_zone="UTC", strict=False)

    def opt(name: str, dtype: pl.DataType) -> pl.Expr:
        return pl.col(name).cast(dtype) if name in names else pl.lit(None, dtype=dtype)

    success = (
        pl.col("success").cast(pl.String).str.to_lowercase() == "true"
        if "success" in names
        else pl.lit(None)
    )
    return df.select(
        ts.alias("timestamp"),
        pl.col("label").cast(pl.String),
        pl.col("elapsed").cast(pl.Float64).alias("latency_ms"),
        success.cast(pl.Boolean).alias("success"),
        opt("bytes", pl.Int64()).alias("bytes"),
        opt("allThreads", pl.Int64()).alias("threads"),
        opt("responseCode", pl.String()).alias("response_code"),
        pl.lit("UTC").alias("orig_tz"),
    )


def parse_jtl_csv(path: Path, detection: Detection, ctx: ParseContext) -> Parsed:
    overrides: dict[str, pl.DataType] = {
        "elapsed": pl.Int64(),
        "responseCode": pl.String(),
        "label": pl.String(),
    }
    batches = _csv_batches(path, detection.delimiter or ",", overrides)
    return Parsed(DatasetKind.PERF_SAMPLES, (_jtl_csv_frame(b) for b in batches))


def parse_jtl_xml(path: Path, ctx: ParseContext) -> Parsed:
    rows: dict[str, list[Any]] = {k: [] for k in PERF_SAMPLES_SCHEMA}
    try:
        for _, el in etree.iterparse(str(path), events=("end",), huge_tree=True):
            if _local(el.tag) not in {"httpSample", "sample"}:
                continue
            ts = el.get("ts")
            rows["timestamp"].append(datetime.fromtimestamp(int(ts) / 1000, UTC) if ts else None)
            rows["label"].append(el.get("lb"))
            rows["latency_ms"].append(float(el.get("t")) if el.get("t") else None)
            rows["success"].append((el.get("s") or "").lower() == "true" if el.get("s") else None)
            rows["bytes"].append(int(el.get("by")) if el.get("by") else None)
            rows["threads"].append(int(el.get("na")) if el.get("na") else None)
            rows["response_code"].append(el.get("rc"))
            rows["orig_tz"].append("UTC")
            el.clear()
    except etree.XMLSyntaxError as exc:
        raise IngestionError(
            f"The JMeter XML file is malformed (line {exc.lineno}): {exc.msg}."
        ) from None
    except ValueError as exc:
        raise readable("A sample in the JMeter XML file has an unreadable value", exc) from None
    if not rows["label"]:
        raise IngestionError("The JMeter XML file has no samples.")
    return Parsed(DatasetKind.PERF_SAMPLES, iter([pl.DataFrame(rows, schema=PERF_SAMPLES_SCHEMA)]))


# --------------------------------------------------------------------------- tabular


def parse_delimited(path: Path, detection: Detection, ctx: ParseContext) -> Parsed:
    batches = _csv_batches(path, detection.delimiter or ",")
    return Parsed(DatasetKind.GENERIC, normalize_generic(batches, ctx.default_tz))


def parse_json(path: Path, ctx: ParseContext) -> Parsed:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise IngestionError(
            f"The JSON file is malformed (line {exc.lineno}, column {exc.colno}): {exc.msg}."
        ) from None
    if isinstance(doc, dict):
        arrays = [v for v in doc.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(arrays) != 1:
            raise IngestionError(
                "The JSON file should be a list of records, or an object holding one list."
            )
        doc = arrays[0]
    if not isinstance(doc, list) or not doc or not all(isinstance(r, dict) for r in doc):
        raise IngestionError("The JSON file should be a non-empty list of records (objects).")
    frame = pl.json_normalize(doc, separator=".", infer_schema_length=None)
    return Parsed(DatasetKind.GENERIC, normalize_generic(iter([frame]), ctx.default_tz))


def parse_ndjson(path: Path, ctx: ParseContext) -> Parsed:
    return Parsed(DatasetKind.GENERIC, normalize_generic(_ndjson_batches(path), ctx.default_tz))


def parse_xlsx(path: Path, ctx: ParseContext) -> Parsed:
    try:
        frame = pl.read_excel(path, sheet_id=1)
    except Exception as exc:
        raise readable("The Excel file is damaged or isn't a real .xlsx workbook", exc) from None
    return Parsed(DatasetKind.GENERIC, normalize_generic(iter([frame]), ctx.default_tz))


# --------------------------------------------------------------------------- dispatch

_LABELS = {
    Format.CSV: "CSV",
    Format.TSV: "TSV",
    Format.JSON: "JSON",
    Format.NDJSON: "NDJSON",
    Format.XLSX: "Excel",
    Format.JUNIT_XML: "JUnit XML",
    Format.JTL_CSV: "JMeter CSV",
    Format.JTL_XML: "JMeter XML",
}


def parse(path: Path, detection: Detection, ctx: ParseContext) -> Parsed:
    """Parse ``path`` as ``detection.format`` or raise IngestionError."""
    handlers: dict[Format, Callable[[], Parsed]] = {
        Format.CSV: lambda: parse_delimited(path, detection, ctx),
        Format.TSV: lambda: parse_delimited(path, detection, ctx),
        Format.JSON: lambda: parse_json(path, ctx),
        Format.NDJSON: lambda: parse_ndjson(path, ctx),
        Format.XLSX: lambda: parse_xlsx(path, ctx),
        Format.JUNIT_XML: lambda: parse_junit(path, ctx),
        Format.JTL_CSV: lambda: parse_jtl_csv(path, detection, ctx),
        Format.JTL_XML: lambda: parse_jtl_xml(path, ctx),
    }
    handler = handlers.get(detection.format)
    if handler is None:
        raise IngestionError(detection.reason)
    try:
        return handler()
    except IngestionError:
        raise
    except Exception as exc:  # library errors become readable messages
        raise readable(f"The {_LABELS[detection.format]} file couldn't be read", exc) from None


def write_parquet(parsed: Parsed, out: Path, fmt: Format) -> tuple[pl.Schema, int]:
    """Write the batches to one Parquet file; return (schema, row count)."""
    writer: pq.ParquetWriter | None = None
    schema: pa.Schema | None = None
    rows = 0
    try:
        for df in parsed.batches:
            if df.height == 0:
                continue
            table = df.to_arrow()
            if writer is None or schema is None:
                schema = table.schema
                writer = pq.ParquetWriter(out, schema, compression="zstd")
            elif table.schema != schema:
                table = table.cast(schema)
            writer.write_table(table)
            rows += df.height
    except IngestionError:
        raise
    except Exception as exc:
        raise readable(f"The {_LABELS.get(fmt, 'uploaded')} file couldn't be read", exc) from None
    finally:
        if writer is not None:
            writer.close()
    if rows == 0:
        raise IngestionError("The file has a header but no data rows.")
    return pl.Schema(pl.read_parquet_schema(out)), rows
