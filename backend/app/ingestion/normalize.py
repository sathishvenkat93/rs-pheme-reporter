"""Normalisation into canonical tables, plus helpers shared by the parsers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from zoneinfo import ZoneInfo

import polars as pl

from app.models.dataset import ColumnSchema, ColumnType

TEST_RESULTS_SCHEMA: dict[str, pl.DataType] = {
    "run_id": pl.String(),
    "build": pl.String(),
    "suite": pl.String(),
    "case": pl.String(),
    "status": pl.String(),
    "duration_ms": pl.Float64(),
    "error_type": pl.String(),
    "error_message": pl.String(),
    "timestamp": pl.Datetime("us", "UTC"),
    "orig_tz": pl.String(),
}

PERF_SAMPLES_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime("us", "UTC"),
    "label": pl.String(),
    "latency_ms": pl.Float64(),
    "success": pl.Boolean(),
    "bytes": pl.Int64(),
    "threads": pl.Int64(),
    "response_code": pl.String(),
    "orig_tz": pl.String(),
}

_STATUS = {
    "passed": "passed", "pass": "passed", "ok": "passed", "success": "passed",
    "successful": "passed", "succeeded": "passed", "green": "passed",
    "failed": "failed", "fail": "failed", "failure": "failed", "red": "failed",
    "error": "error", "errored": "error", "broken": "error",
    "skipped": "skipped", "skip": "skipped", "ignored": "skipped", "disabled": "skipped",
    "pending": "skipped", "notrun": "skipped",
}  # fmt: skip


def standard_status(raw: str | None) -> str:
    """pass/ok/success → passed, fail → failed, and so on. Unknown values pass through lowercased."""
    if raw is None:
        return "unknown"
    key = re.sub(r"[\s_-]", "", raw.strip().lower())
    return _STATUS.get(key, raw.strip().lower() or "unknown")


@dataclass(frozen=True, slots=True)
class ParseContext:
    """What a parser needs to know beyond the file's bytes."""

    upload_id: str
    filename: str
    build: str | None  # explicit, from the upload form
    build_order: int  # fallback: position of this upload in its dataset
    default_tz: str


_BUILD_IN_NAME = re.compile(r"(?:build|bld|run|b)[-_ #.]?(\d+)", re.IGNORECASE)


def build_from_filename(filename: str) -> str | None:
    stem = PurePath(filename).stem
    m = _BUILD_IN_NAME.search(stem)
    if m:
        return m.group(1)
    nums = re.findall(r"\d+", stem)
    return nums[-1] if nums else None


_OFFSET = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def parse_timestamp(raw: str | None, default_tz: str) -> tuple[datetime | None, str | None]:
    """ISO-8601 text → (UTC datetime, original zone). Naive values use ``default_tz``."""
    if not raw:
        return None, None
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None, None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(default_tz)).astimezone(UTC), default_tz
    m = _OFFSET.search(text)
    return dt.astimezone(UTC), (
        "UTC" if m and m.group(1) == "Z" else m.group(1) if m else str(dt.tzinfo)
    )


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$"
)


def _clean_names(names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    seen: set[str] = set()
    for i, name in enumerate(names):
        base = name.strip() or f"column_{i + 1}"
        new, n = base, 2
        while new in seen:
            new, n = f"{base}_{n}", n + 1
        seen.add(new)
        out[name] = new
    return out


class GenericPlan:
    """How to type a generic table, decided once from its first batch.

    Every later batch gets the same renames and casts, so batches stay consistent.
    """

    def __init__(self, first: pl.DataFrame, default_tz: str) -> None:
        self.rename = _clean_names(first.columns)
        sample = first.rename(self.rename).head(1000)
        self.exprs: list[pl.Expr] = []
        tz_source: pl.Expr | None = None
        for name, dtype in sample.schema.items():
            col = pl.col(name)
            if isinstance(dtype, pl.Struct):
                self.exprs.append(col.struct.json_encode().alias(name))
            elif isinstance(dtype, pl.List | pl.Array):
                self.exprs.append(col.cast(pl.List(pl.String)).list.join(", ").alias(name))
            elif dtype == pl.Null:
                self.exprs.append(col.cast(pl.String).alias(name))
            elif isinstance(dtype, pl.Datetime):
                zone = dtype.time_zone
                expr = col.dt.replace_time_zone(default_tz) if zone is None else col
                self.exprs.append(expr.dt.convert_time_zone("UTC").alias(name))
                tz_source = tz_source if tz_source is not None else pl.lit(zone or default_tz)
            elif dtype == pl.String:
                values = [
                    v
                    for v in sample[name].drop_nulls().to_list()
                    if isinstance(v, str) and v.strip()
                ]
                if not values:
                    continue
                if sum(bool(_ISO_DATE.match(v)) for v in values) / len(values) >= 0.95:
                    self.exprs.append(col.str.to_date("%Y-%m-%d", strict=False).alias(name))
                elif sum(bool(_ISO_DATETIME.match(v)) for v in values) / len(values) >= 0.95:
                    if any(_OFFSET.search(v) for v in values):
                        self.exprs.append(
                            col.str.to_datetime(time_zone="UTC", strict=False).alias(name)
                        )
                        if tz_source is None:
                            tz_source = (
                                col.str.extract(r"(Z|[+-]\d{2}:?\d{2})$")
                                .replace({"Z": "UTC"})
                                .fill_null(pl.lit(default_tz))
                            )
                    else:
                        self.exprs.append(
                            col.str.to_datetime(strict=False)
                            .dt.replace_time_zone(default_tz)
                            .dt.convert_time_zone("UTC")
                            .alias(name)
                        )
                        tz_source = tz_source if tz_source is not None else pl.lit(default_tz)
        if tz_source is not None and "orig_tz" not in sample.columns:
            self.exprs.append(tz_source.alias("orig_tz"))

    def apply(self, df: pl.DataFrame) -> pl.DataFrame:
        df = df.rename(self.rename)
        return df.with_columns(self.exprs) if self.exprs else df


def normalize_generic(batches: Iterator[pl.DataFrame], default_tz: str) -> Iterator[pl.DataFrame]:
    """Typed generic table: clean names, flatten nested values, ISO text → dates/UTC timestamps."""
    plan: GenericPlan | None = None
    for df in batches:
        if plan is None:
            plan = GenericPlan(df, default_tz)
        yield plan.apply(df)


def column_type(dtype: pl.DataType) -> ColumnType:
    if dtype == pl.Boolean:
        return ColumnType.BOOLEAN
    if dtype.is_integer():
        return ColumnType.INTEGER
    if dtype.is_float() or dtype.is_decimal():
        return ColumnType.FLOAT
    if isinstance(dtype, pl.Datetime):
        return ColumnType.TIMESTAMP
    if dtype == pl.Date:
        return ColumnType.DATE
    return ColumnType.STRING


def columns_of(schema: pl.Schema) -> list[ColumnSchema]:
    return [ColumnSchema(name=n, type=column_type(t)) for n, t in schema.items()]
