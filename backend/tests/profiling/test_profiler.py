from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import polars as pl
import pytest

from app.domain_packs.loader import all_packs, load_pack
from app.models.dataset import ColumnRole, ColumnType, DatasetKind
from app.profiling.llm_view import BUDGET_BYTES, llm_view, size
from app.profiling.profiler import best_split, mann_kendall_tau, profile_paths
from app.profiling.roles import ColumnFacts, infer_role

from ..profiling_helpers import fixture_parquet

SNAPSHOTS = Path(__file__).parent / "snapshots"
VERSION = uuid.UUID(int=1)
DATASETS = {
    "junit": [f"junit-build-{b}.xml" for b in range(101, 106)],
    "jtl": ["jmeter-load.jtl"],
    "jtl_xml": ["jmeter-small-xml.jtl"],
    "daily_metrics": ["daily-metrics.csv"],
    "deployments": ["deployments.json"],
    "events": ["events.ndjson"],
    "inventory": ["inventory.xlsx"],
}


def _profile(key: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    paths, kind = fixture_parquet(DATASETS[key], tmp_path)
    return profile_paths(paths, kind, VERSION)


@pytest.mark.parametrize("key", sorted(DATASETS))
def test_profile_snapshot(key: str, tmp_path: Path) -> None:
    actual = json.loads(_profile(key, tmp_path).model_dump_json())
    snap = SNAPSHOTS / f"{key}.json"
    if os.environ.get("PHEME_UPDATE_SNAPSHOTS") == "1" or not snap.exists():
        snap.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snap.read_text()), (
        "profile changed; rerun with PHEME_UPDATE_SNAPSHOTS=1 if intended"
    )


@pytest.mark.parametrize(
    ("key", "domain"),
    [
        ("junit", "test_execution"),
        ("jtl", "performance"),
        ("daily_metrics", "generic"),
        ("events", "app_logs"),
        ("deployments", "cicd"),
    ],
)
def test_domain_classification(key: str, domain: str, tmp_path: Path) -> None:
    p = _profile(key, tmp_path)
    assert p.domain == domain
    assert p.domain_confidence is not None
    assert p.domain_confidence >= 0.6


def test_junit_profile_facts(tmp_path: Path) -> None:
    p = _profile("junit", tmp_path)
    roles = {c.name: c.role for c in p.columns}
    assert roles["status"] is ColumnRole.STATUS
    assert roles["duration_ms"] is ColumnRole.DURATION
    assert roles["timestamp"] is ColumnRole.TIME
    assert roles["run_id"] is ColumnRole.IDENTIFIER
    assert ["suite", "case"] in p.hierarchies
    ts = next(c for c in p.columns if c.name == "timestamp")
    assert ts.time is not None
    assert ts.time.monotonic


def test_daily_metrics_key_and_granularity(tmp_path: Path) -> None:
    p = _profile("daily_metrics", tmp_path)
    assert ["date", "region"] in p.candidate_keys
    date = next(c for c in p.columns if c.name == "date")
    assert date.time is not None
    assert date.time.median_gap_seconds == 86400


def test_profiling_the_jtl_fixture_takes_under_a_second(tmp_path: Path) -> None:
    paths, kind = fixture_parquet(DATASETS["jtl"], tmp_path)
    profile_paths(paths, kind, VERSION)  # warm-up (imports, DuckDB init)
    start = time.perf_counter()
    profile_paths(paths, kind, VERSION)
    assert time.perf_counter() - start < 1.0


def test_llm_view_of_a_50_column_dataset_fits_the_budget(tmp_path: Path) -> None:
    n = 400
    cols: dict[str, list[object]] = {
        "timestamp": pl.datetime_range(
            pl.datetime(2026, 1, 1),
            pl.datetime(2026, 1, 1) + pl.duration(minutes=n - 1),
            "1m",
            eager=True,
            time_zone="UTC",
        ).to_list()
    }
    for i in range(20):
        cols[f"metric_value_number_{i:02d}_ms"] = [float((j * (i + 3)) % 997) for j in range(n)]
    for i in range(29):
        cols[f"category_dimension_{i:02d}"] = [
            f"value_{(j * (i + 1)) % 12}_with_a_longish_name" for j in range(n)
        ]
    path = tmp_path / "wide.parquet"
    pl.DataFrame(cols).write_parquet(path)
    p = profile_paths([path], DatasetKind.GENERIC, VERSION)
    assert len(p.columns) == 50
    assert size(llm_view(p)) <= BUDGET_BYTES


@pytest.mark.parametrize(
    ("facts", "role"),
    [
        (ColumnFacts("latency_ms", ColumnType.FLOAT, 100, 100, 90), ColumnRole.DURATION),
        (ColumnFacts("test_duration", ColumnType.FLOAT, 100, 100, 90), ColumnRole.DURATION),
        (ColumnFacts("status", ColumnType.STRING, 100, 100, 4), ColumnRole.STATUS),
        (ColumnFacts("result", ColumnType.STRING, 100, 100, 3), ColumnRole.STATUS),
        (
            ColumnFacts("status", ColumnType.STRING, 100, 100, 40),
            ColumnRole.CATEGORY,
        ),  # too many values
        (
            ColumnFacts("session", ColumnType.STRING, 100, 100, 97),
            ColumnRole.IDENTIFIER,
        ),  # mostly unique
        (ColumnFacts("user_id", ColumnType.INTEGER, 100, 100, 20), ColumnRole.IDENTIFIER),
        (ColumnFacts("error_rate", ColumnType.FLOAT, 100, 100, 50), ColumnRole.RATIO),
        (ColumnFacts("share", ColumnType.FLOAT, 100, 100, 50, min=0.1, max=0.9), ColumnRole.RATIO),
        (ColumnFacts("score", ColumnType.FLOAT, 100, 100, 50, min=0.0, max=0.8), ColumnRole.RATIO),
        (ColumnFacts("request_count", ColumnType.INTEGER, 100, 100, 50), ColumnRole.COUNT),
        (
            ColumnFacts("revenue", ColumnType.FLOAT, 100, 100, 99, min=3, max=900),
            ColumnRole.MEASURE,
        ),
        (ColumnFacts("created", ColumnType.TIMESTAMP, 100, 100, 100), ColumnRole.TIME),
        (ColumnFacts("country", ColumnType.STRING, 100, 100, 8), ColumnRole.GEO),
        (ColumnFacts("note", ColumnType.STRING, 100, 100, 60, avg_len=120), ColumnRole.TEXT),
        (ColumnFacts("region", ColumnType.STRING, 100, 100, 3), ColumnRole.CATEGORY),
        (ColumnFacts("success", ColumnType.BOOLEAN, 100, 100, 2), ColumnRole.STATUS),
    ],
)
def test_role_rules(facts: ColumnFacts, role: ColumnRole) -> None:
    assert infer_role(facts) is role


def test_trend_and_change_point_helpers() -> None:
    assert mann_kendall_tau([1, 2, 3, 4, 5]) == 1.0
    assert mann_kendall_tau([5, 4, 3, 2, 1]) == -1.0
    assert mann_kendall_tau([1, 1, 1]) == 0.0
    split = best_split([1, 1, 1, 1, 9, 9, 9, 9])
    assert split is not None
    assert split[0] == 4
    assert split[1] > 1


def test_domain_packs_load_and_reference_valid_measures() -> None:
    domains = {p.domain for p in all_packs()}
    assert domains == {"test_execution", "performance", "app_logs", "cicd", "generic"}
    te, perf = load_pack("test_execution"), load_pack("performance")
    assert {k.id for k in te.kpis} == {
        "pass_rate",
        "failed_count",
        "flaky_rate",
        "total_duration",
        "avg_duration",
    }
    assert {"p50", "p90", "p95", "p99", "throughput", "error_rate", "apdex"} <= {
        k.id for k in perf.kpis
    }
    assert load_pack("unknown_domain").domain == "generic"
