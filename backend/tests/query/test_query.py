from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.core.config import get_settings
from app.domain_packs.loader import load_pack
from app.ingestion import storage
from app.ingestion.normalize import columns_of
from app.models.dataset import ColumnType
from app.models.query import Measure, SemanticQuery
from app.query import engine
from app.query.compiler import OTHER, QueryError, compile_query
from app.query.executor import Target, default_time_field, execute, run_compiled
from app.query.lttb import lttb

from ..profiling_helpers import fixture_parquet

JUNIT = [f"junit-build-{b}.xml" for b in range(101, 106)]


def _cols(path: Path) -> dict[str, ColumnType]:
    return {c.name: c.type for c in columns_of(pl.Schema(pl.read_parquet_schema(path)))}


def run(
    paths: list[Path], q: dict[str, Any], measures: Sequence[Measure], cap: int = 10_000
) -> list[list[Any]]:
    cols = _cols(paths[0])
    compiled = compile_query(
        SemanticQuery(dataset="ds", **q),
        cols,
        {m.id: m for m in measures},
        time_field=default_time_field(cols),
    )
    rows, _ = run_compiled(paths, compiled, timeout_s=30, row_cap=cap)
    return rows


@pytest.fixture(scope="module")
def junit(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    return fixture_parquet(JUNIT, tmp_path_factory.mktemp("junit"))[0]


@pytest.fixture(scope="module")
def jtl(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    return fixture_parquet(["jmeter-load.jtl"], tmp_path_factory.mktemp("jtl"))[0]


TE = load_pack("test_execution").measures
PERF = load_pack("performance").measures


# ----------------------------------------------------------------------------- golden


def test_pass_rate_by_build_matches_polars(junit: list[Path]) -> None:
    rows = run(
        junit, {"dimensions": ["build"], "measures": ["pass_rate"], "time_grain": "build"}, TE
    )
    df = pl.read_parquet(junit)
    expected = (
        df.filter(pl.col("status") != "skipped")
        .group_by("build")
        .agg((pl.col("status") == "passed").mean().alias("rate"))
        .sort("build")
    )
    assert [r[0] for r in rows] == expected["build"].to_list()
    for (_, got), want in zip(rows, expected["rate"].to_list(), strict=True):
        assert got == pytest.approx(want, rel=1e-12)


def test_p95_latency_per_label_matches_polars(jtl: list[Path]) -> None:
    rows = run(jtl, {"dimensions": ["label"], "measures": ["p95"]}, PERF)
    df = pl.read_parquet(jtl)
    expected = dict(
        df.group_by("label")
        .agg(pl.col("latency_ms").quantile(0.95, interpolation="linear"))
        .iter_rows()
    )
    assert len(rows) == 8
    for label, got in rows:
        assert got == pytest.approx(expected[label], rel=1e-12)


def test_flaky_rate_matches_polars(junit: list[Path]) -> None:
    (rate,) = run(junit, {"measures": ["flaky_rate"]}, TE)[0]
    df = pl.read_parquet(junit)
    per = df.group_by("build", "suite", "case").agg(
        (
            (pl.col("status") == "passed").any() & pl.col("status").is_in(["failed", "error"]).any()
        ).alias("f")
    )
    assert rate == pytest.approx(per["f"].mean())
    assert rate > 0


def test_last_n_builds_and_relative_time(junit: list[Path]) -> None:
    rows = run(
        junit,
        {
            "dimensions": ["build"],
            "measures": ["runs"],
            "filters": [{"op": "last_n", "field": "build", "n": 2}],
        },
        TE,
    )
    assert [r[0] for r in rows] == ["104", "105"]
    rows = run(
        junit,
        {"dimensions": ["timestamp"], "measures": ["runs"], "time_grain": "day",
         "filters": [{"op": "relative_time", "field": "timestamp", "last": 1, "unit": "day"}]},
        TE,
    )  # fmt: skip
    assert [r[0][:10] for r in rows] == [
        "2026-10-04",
        "2026-10-05",
    ]  # anchored to the data's last timestamp


def test_rate_and_apdex(jtl: list[Path]) -> None:
    df = pl.read_parquet(jtl)
    (throughput, apdex) = run(jtl, {"measures": ["throughput", "apdex"]}, PERF)[0]
    span = df.select(
        (pl.col("timestamp").max() - pl.col("timestamp").min()).dt.total_seconds(fractional=True)
    ).item()
    assert throughput == pytest.approx(len(df) / span)
    sat = int((df["latency_ms"] <= 500).sum())
    tol = int(((df["latency_ms"] > 500) & (df["latency_ms"] <= 2000)).sum())
    assert apdex == pytest.approx((sat + tol / 2) / len(df))
    per_min = run(
        jtl, {"dimensions": ["timestamp"], "measures": ["throughput"], "time_grain": "minute"}, PERF
    )
    assert per_min[0][1] == pytest.approx(200 / 60)  # 200 samples per minute in the fixture


def test_row_cap_truncates(jtl: list[Path]) -> None:
    cols = _cols(jtl[0])
    q = SemanticQuery(dataset="ds", dimensions=["timestamp", "label"])
    compiled = compile_query(q, cols, {}, row_cap=10)
    rows, truncated = run_compiled(jtl, compiled, timeout_s=30, row_cap=10)
    assert len(rows) == 10
    assert truncated


# ----------------------------------------------------------------------------- safety


@pytest.mark.parametrize(
    "field",
    ['status"; DROP TABLE data; --', "status' OR '1'='1", "read_csv('/etc/passwd')", "nope"],
)
def test_unknown_or_malicious_field_names_are_refused(junit: list[Path], field: str) -> None:
    with pytest.raises(QueryError, match="no field named"):
        run(junit, {"dimensions": [field], "measures": ["runs"]}, TE)


@pytest.mark.parametrize(
    "value",
    [
        "x'; DROP TABLE data; --",
        "passed' OR '1'='1",
        "1); SELECT * FROM read_csv('/etc/passwd'); --",
    ],
)
def test_filter_values_are_bound_not_spliced(junit: list[Path], value: str) -> None:
    rows = run(
        junit,
        {"measures": ["runs"], "filters": [{"op": "eq", "field": "status", "value": value}]},
        TE,
    )
    assert rows == [[0]]
    assert run(junit, {"measures": ["runs"]}, TE)[0][0] == 177  # table intact


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT count(*) FROM read_parquet('/tmp/other.parquet')",
        "SELECT * FROM read_parquet('https://example.com/x.parquet')",
        "INSERT INTO data VALUES (1)",
        "INSTALL httpfs",
        "SET enable_external_access = true",
        "COPY (SELECT 1) TO '/tmp/x.csv'",
    ],
)
def test_executor_connection_refuses_files_network_and_writes(junit: list[Path], sql: str) -> None:
    con = engine.connect(junit)
    try:
        with pytest.raises(Exception):  # noqa: B017 - any DuckDB refusal is a pass
            con.execute(sql).fetchall()
        assert con.execute("SELECT count(*) FROM data").fetchone() == (177,)
    finally:
        con.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP VIEW data",
        "DROP VIEW data",
        "INSERT INTO data VALUES (1)",
        "CREATE TABLE t AS SELECT 1",
    ],
)
def test_executor_runs_only_a_single_select(junit: list[Path], sql: str) -> None:
    from app.query.compiler import Compiled

    with pytest.raises(QueryError, match="single read-only query"):
        run_compiled(junit, Compiled(sql=sql), timeout_s=30, row_cap=10)


def test_measure_ids_and_sort_keys_are_validated(junit: list[Path]) -> None:
    with pytest.raises(QueryError, match="isn't defined"):
        run(junit, {"measures": ["mystery"]}, TE)
    with pytest.raises(QueryError, match="Can't sort"):
        run(
            junit, {"dimensions": ["build"], "measures": ["runs"], "sort": [{"field": "suite"}]}, TE
        )


# ----------------------------------------------------------------------------- top_n + Other

CATS = [f"c{i:02d}" for i in range(15)]


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[Path], pl.DataFrame]:
    import random

    rng = random.Random(3)
    df = pl.DataFrame(
        {
            "cat": [rng.choice(CATS[: rng.randint(1, 15)]) for _ in range(3000)],
            "v": [round(rng.uniform(0, 100), 3) for _ in range(3000)],
            "g": [rng.choice(["a", "b"]) for _ in range(3000)],
        }
    )
    path = tmp_path_factory.mktemp("syn") / "syn.parquet"
    df.write_parquet(path)
    return [path], df


SYN_MEASURES = [
    Measure(id="n", expr="count()"),
    Measure(id="total", expr="sum(v)"),
    Measure(id="mean", expr="avg(v)"),
    Measure(id="p90", expr="percentile(v, 90)"),
]


@settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(top_n=st.integers(1, 16), measure=st.sampled_from(["n", "total"]), split=st.booleans())
def test_top_n_plus_other_sums_to_the_total_for_additive_measures(
    synthetic: tuple[list[Path], pl.DataFrame], top_n: int, measure: str, split: bool
) -> None:
    paths, _ = synthetic
    dims = ["cat", "g"] if split else ["cat"]
    grouped = run(paths, {"dimensions": dims, "measures": [measure], "top_n": top_n}, SYN_MEASURES)
    (total,) = run(paths, {"measures": [measure]}, SYN_MEASURES)[0]
    assert sum(r[-1] for r in grouped) == pytest.approx(total)
    assert len({r[0] for r in grouped} - {OTHER}) <= top_n


@settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(top_n=st.integers(1, 14), measure=st.sampled_from(["mean", "p90"]))
def test_other_is_the_measure_over_excluded_rows_for_non_additive_measures(
    synthetic: tuple[list[Path], pl.DataFrame], top_n: int, measure: str
) -> None:
    paths, df = synthetic
    rows = run(paths, {"dimensions": ["cat"], "measures": [measure], "top_n": top_n}, SYN_MEASURES)
    kept = {r[0] for r in rows} - {OTHER}
    rest = df.filter(~pl.col("cat").is_in(list(kept)))
    other = [r[1] for r in rows if r[0] == OTHER]
    if rest.height == 0:
        assert other == []
        return
    want = (
        rest["v"].mean() if measure == "mean" else rest["v"].quantile(0.9, interpolation="linear")
    )
    assert other == [pytest.approx(want)]


# ----------------------------------------------------------------------------- cache


def test_cached_query_does_not_touch_duckdb(
    jtl: list[Path], redis_conn: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = f"tests/{uuid.uuid4()}/part.parquet"
    storage.put_file(key, jtl[0])
    target = Target(
        version_id=uuid.uuid4(),
        parts=[key],
        columns=columns_of(pl.Schema(pl.read_parquet_schema(jtl[0]))),
    )
    q = SemanticQuery(dataset="ds", dimensions=["label"], measures=["p95"])

    calls = {"n": 0}
    real = engine.connect

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "connect", spy)
    first = execute(target, q, PERF)
    second = execute(target, q, PERF)
    assert calls["n"] == 1
    assert not first.cached
    assert second.cached
    assert second.rows == first.rows
    assert second.query_hash == first.query_hash
    assert get_settings().query_cache_ttl_s > 0


# ----------------------------------------------------------------------------- LTTB


def test_lttb_keeps_size_endpoints_and_peaks() -> None:
    pts = [(float(i), float(i % 50 == 0) * 100) for i in range(10_000)]
    out = lttb(pts, 2000)
    assert len(out) == 2000
    assert out[0] == pts[0]
    assert out[-1] == pts[-1]
    assert sum(1 for _, y in out if y == 100) >= 150  # spikes survive
    assert lttb(pts[:100], 2000) == pts[:100]


def test_long_queries_are_interrupted(junit: list[Path]) -> None:
    import time

    from app.query.compiler import Compiled

    slow = Compiled(sql="SELECT sum(a.range * b.range) FROM range(200000) a, range(200000) b")
    start = time.perf_counter()
    with pytest.raises(QueryError, match="longer than"):
        run_compiled(junit, slow, timeout_s=0.2, row_cap=10)
    assert time.perf_counter() - start < 5
