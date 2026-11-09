from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from app.ingestion.detect import HEAD_BYTES, detect
from app.ingestion.errors import IngestionError
from app.ingestion.normalize import (
    PERF_SAMPLES_SCHEMA,
    TEST_RESULTS_SCHEMA,
    ParseContext,
    build_from_filename,
    parse_timestamp,
    standard_status,
)
from app.ingestion.parsers import parse, write_parquet

from .helpers import FIXTURES, JUNIT, MALFORMED, MANIFEST, VALID, assert_readable


def ingest_local(
    name: str, tmp_path: Path, build: str | None = None, order: int = 1
) -> pl.DataFrame:
    path = FIXTURES / name
    d = detect(name, path.read_bytes()[:HEAD_BYTES])
    parsed = parse(path, d, ParseContext("u-1", name, build, order, "Europe/Berlin"))
    out = tmp_path / f"{name}.parquet"
    _, rows = write_parquet(parsed, out, d.format)
    df = pl.read_parquet(out)
    assert df.height == rows
    return df


@pytest.mark.parametrize("name", VALID)
def test_every_row_of_valid_fixtures_parses(name: str, tmp_path: Path) -> None:
    df = ingest_local(name, tmp_path)
    assert df.height == MANIFEST[name]["rows"]


@pytest.mark.parametrize("name", MALFORMED)
def test_malformed_files_fail_with_a_readable_error(name: str, tmp_path: Path) -> None:
    with pytest.raises(IngestionError) as info:
        ingest_local(name, tmp_path)
    assert_readable(info.value.user_message)


def test_junit_normalises_to_test_results(tmp_path: Path) -> None:
    frames = [ingest_local(n, tmp_path) for n in JUNIT]
    df = pl.concat(frames)
    assert dict(df.schema) == TEST_RESULTS_SCHEMA
    assert set(df["suite"]) == {
        "api",
        "ui",
        "db",
    }  # nested <testsuite name="all"> uses the innermost
    assert set(df["status"]) <= {"passed", "failed", "error", "skipped"}
    assert df.filter(pl.col("status").is_in(["failed", "error"]))["case"].n_unique() >= 12
    assert df.filter(pl.col("status") == "failed")["error_type"].n_unique() >= 3
    assert df["case"].n_unique() >= 30
    # Builds come from <properties> (101-103) or the filename (104-105).
    assert sorted(df["build"].unique()) == ["101", "102", "103", "104", "105"]
    # Flaky: a case that both failed and passed within one build.
    flaky = (
        df.group_by("build", "case")
        .agg(pl.col("status").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
    )
    assert flaky.height >= 1
    # Seconds → ms; naive suite timestamps are local (Europe/Berlin) → UTC.
    first = frames[0].row(0, named=True)
    assert first["timestamp"] == datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
    assert first["orig_tz"] == "Europe/Berlin"
    assert first["duration_ms"] == pytest.approx(
        float(
            next(
                line
                for line in (FIXTURES / JUNIT[0]).read_text().splitlines()
                if "<testcase" in line
            )
            .split('time="')[1]
            .split('"')[0]
        )
        * 1000
    )


def test_build_precedence(tmp_path: Path) -> None:
    # Explicit upload field beats <properties> and the filename.
    assert set(ingest_local("junit-build-101.xml", tmp_path, build="9000")["build"]) == {"9000"}
    # The filename is used when there's no property; upload order is the last resort.
    assert build_from_filename("junit-build-104.xml") == "104"
    assert build_from_filename("report.xml") is None


def test_jtl_normalises_to_perf_samples(tmp_path: Path) -> None:
    for name in ("jmeter-load.jtl", "jmeter-small-xml.jtl"):
        df = ingest_local(name, tmp_path)
        assert dict(df.schema) == PERF_SAMPLES_SCHEMA
        assert df["label"].n_unique() == 8 or name.endswith("xml.jtl")
        assert df["success"].dtype == pl.Boolean
        assert (~df["success"]).sum() > 0
        assert df["timestamp"].min() == datetime(2026, 10, 5, 14, 0, tzinfo=UTC) or name.endswith(
            "xml.jtl"
        )


def test_generic_tables_get_typed_columns(tmp_path: Path) -> None:
    daily = ingest_local("daily-metrics.csv", tmp_path)
    assert daily["date"].dtype == pl.Date
    assert daily["orders"].dtype == pl.Int64
    deploys = ingest_local("deployments.json", tmp_path)
    assert deploys["deployed_at"].dtype == pl.Datetime("us", "UTC")
    assert set(deploys["orig_tz"]) == {"+05:30"}
    assert "meta.triggered_by" in deploys.columns
    events = ingest_local("events.ndjson", tmp_path)
    assert events["ts"].dtype == pl.Datetime("us", "UTC")
    assert set(events["orig_tz"]) == {"UTC"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PASS", "passed"),
        ("ok", "passed"),
        ("Success", "passed"),
        ("FAILURE", "failed"),
        ("errored", "error"),
        ("Ignored", "skipped"),
        ("not_run", "skipped"),
        (None, "unknown"),
    ],
)
def test_status_standardisation(raw: str | None, expected: str) -> None:
    assert standard_status(raw) == expected


def test_timestamps_become_utc_with_original_zone() -> None:
    assert parse_timestamp("2026-10-01T09:00:00+05:30", "UTC") == (
        datetime(2026, 10, 1, 3, 30, tzinfo=UTC),
        "+05:30",
    )
    assert parse_timestamp("2026-10-01T09:00:00Z", "UTC") == (
        datetime(2026, 10, 1, 9, tzinfo=UTC),
        "UTC",
    )
    assert parse_timestamp("2026-01-01T09:00:00", "America/New_York") == (
        datetime(2026, 1, 1, 14, tzinfo=UTC),
        "America/New_York",
    )
    assert parse_timestamp("yesterday", "UTC") == (None, None)
