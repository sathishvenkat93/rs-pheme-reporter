"""Dataset profiler: compact, factual statistics computed with DuckDB over Parquet."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb
import numpy as np

from app.models.dataset import (
    ChangePoint,
    ColumnProfile,
    ColumnRole,
    ColumnType,
    Correlation,
    DatasetKind,
    DatasetProfile,
    NumericStats,
    TimeStats,
    TopValue,
    Trend,
)
from app.profiling.domain import classify
from app.profiling.roles import GROUP_ROLES, MEASURE_ROLES, ColumnFacts, infer_role
from app.query.engine import connect, quote

TOP_K = 10
MAX_BUCKETS = 500
MAX_PAIR_COLUMNS = 8


def _sig(x: float | None, digits: int = 6) -> float | None:
    """Round to significant digits so snapshots are stable across platforms."""
    if x is None or not math.isfinite(x):
        return None
    if x == 0:
        return 0.0
    return round(x, -math.floor(math.log10(abs(x))) + digits - 1)


def column_type(duck_type: str) -> ColumnType:
    t = duck_type.upper()
    if t == "BOOLEAN":
        return ColumnType.BOOLEAN
    if t.startswith("TIMESTAMP"):
        return ColumnType.TIMESTAMP
    if t == "DATE":
        return ColumnType.DATE
    if t in {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
    }:
        return ColumnType.INTEGER
    if t in {"FLOAT", "DOUBLE", "REAL"} or t.startswith("DECIMAL"):
        return ColumnType.FLOAT
    return ColumnType.STRING


def _epoch(col: str, ctype: ColumnType) -> str:
    return f"epoch(CAST({col} AS TIMESTAMP))" if ctype == ColumnType.DATE else f"epoch({col})"


def mann_kendall_tau(series: Sequence[float]) -> float:
    x = np.asarray(series, dtype=float)
    n = len(x)
    if n < 3:
        return 0.0
    s = np.sign(x[None, :] - x[:, None])[np.triu_indices(n, 1)].sum()
    return float(s / (n * (n - 1) / 2))


def best_split(series: Sequence[float]) -> tuple[int, float] | None:
    """One-split binary segmentation: (index, effect size) of the strongest mean shift."""
    x = np.asarray(series, dtype=float)
    n = len(x)
    if n < 6 or float(np.std(x)) == 0:
        return None
    best: tuple[int, float] | None = None
    for i in range(3, n - 2):
        a, b = x[:i], x[i:]
        pooled = math.sqrt((a.var() * len(a) + b.var() * len(b)) / n)
        diff = abs(a.mean() - b.mean())
        effect = (
            diff / pooled if pooled else (math.inf if diff else 0.0)
        )  # flat segments: clean step
        if best is None or effect > best[1]:
            best = (i, effect)
    return best


class _Profiler:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    def one(self, sql: str) -> tuple[Any, ...]:
        row = self.con.execute(sql).fetchone()
        assert row is not None
        return row

    def columns(self) -> list[tuple[str, ColumnType]]:
        return [(r[0], column_type(r[1])) for r in self.con.execute("DESCRIBE data").fetchall()]

    def column_facts(self, cols: list[tuple[str, ColumnType]], rows: int) -> list[dict[str, Any]]:
        parts: list[str] = []
        for name, ctype in cols:
            q = quote(name)
            parts += [f"count({q})", f"count(DISTINCT {q})"]
            if ctype in (ColumnType.INTEGER, ColumnType.FLOAT):
                parts += [
                    f"min({q})::DOUBLE", f"max({q})::DOUBLE", f"avg({q})::DOUBLE",
                    f"quantile_cont({q}, 0.5)::DOUBLE", f"quantile_cont({q}, 0.95)::DOUBLE", f"skewness({q})::DOUBLE",
                ]  # fmt: skip
            elif ctype == ColumnType.STRING:
                parts.append(f"avg(length({q}))::DOUBLE")
            elif ctype in (ColumnType.TIMESTAMP, ColumnType.DATE):
                parts += [f"min({_epoch(q, ctype)})::DOUBLE", f"max({_epoch(q, ctype)})::DOUBLE"]
        values = list(self.one("SELECT " + ", ".join(parts) + " FROM data"))
        out: list[dict[str, Any]] = []
        for name, ctype in cols:
            f: dict[str, Any] = {
                "name": name,
                "type": ctype,
                "non_null": values.pop(0),
                "distinct": values.pop(0),
            }
            if ctype in (ColumnType.INTEGER, ColumnType.FLOAT):
                f["numeric"] = dict(
                    zip(("min", "max", "mean", "p50", "p95", "skew"), values[:6], strict=True)
                )
                del values[:6]
            elif ctype == ColumnType.STRING:
                f["avg_len"] = values.pop(0)
            elif ctype in (ColumnType.TIMESTAMP, ColumnType.DATE):
                f["epoch"] = (values.pop(0), values.pop(0))
            f["null_pct"] = 100.0 * (rows - f["non_null"]) / rows if rows else 0.0
            out.append(f)
        return out

    def top_k(self, name: str) -> list[TopValue]:
        q = quote(name)
        rows = self.con.execute(
            f"SELECT CAST({q} AS VARCHAR) AS v, count(*) AS c FROM data WHERE {q} IS NOT NULL "
            f"GROUP BY 1 ORDER BY c DESC, v LIMIT {TOP_K}"
        ).fetchall()
        return [TopValue(value=str(v)[:80], count=int(c)) for v, c in rows]

    def time_stats(self, name: str, ctype: ColumnType, span: tuple[float, float]) -> TimeStats:
        q = quote(name)
        e = _epoch("t", ctype)
        gap = self.one(
            f"SELECT median(g) FROM (SELECT {e} - lag({e}) OVER (ORDER BY t) AS g "
            f"FROM (SELECT DISTINCT {q} AS t FROM data WHERE {q} IS NOT NULL))"
        )[0]
        er = _epoch(q, ctype)
        mono = self.one(
            f"SELECT coalesce(bool_and(d >= 0), true) FROM (SELECT {er} - lag({er}) OVER "
            f"(PARTITION BY __file ORDER BY __row) AS d FROM data_rows)"
        )[0]
        lo, hi = span
        return TimeStats(
            span_seconds=_sig(max(0.0, (hi or 0) - (lo or 0))) or 0.0,
            median_gap_seconds=_sig(float(gap)) if gap is not None else None,
            monotonic=bool(mono),
        )


def _series(
    p: _Profiler, time_col: str, ttype: ColumnType, measure: str, span: tuple[float, float]
) -> list[float]:
    lo, hi = span
    if lo is None or hi is None or hi <= lo:
        return []
    width = (hi - lo) / MAX_BUCKETS
    e = _epoch(quote(time_col), ttype)
    rows = p.con.execute(
        f"SELECT least(floor(({e} - {lo}) / {width}), {MAX_BUCKETS - 1}) AS b, avg({quote(measure)})::DOUBLE "
        f"FROM data WHERE {quote(measure)} IS NOT NULL AND {quote(time_col)} IS NOT NULL GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return [float(v) for _, v in rows if v is not None]


def profile_paths(paths: Sequence[Path], kind: DatasetKind, version_id: UUID) -> DatasetProfile:
    """Profile the Parquet ``paths`` of one dataset version."""
    con = connect(paths, row_numbers=True)
    try:
        return _profile(_Profiler(con), kind, version_id)
    finally:
        con.close()


def _profile(p: _Profiler, kind: DatasetKind, version_id: UUID) -> DatasetProfile:
    rows = int(p.one("SELECT count(*) FROM data")[0])
    cols = p.columns()
    facts = p.column_facts(cols, rows)

    profiles: list[ColumnProfile] = []
    spans: dict[str, tuple[float, float]] = {}
    for f in facts:
        num = f.get("numeric")
        role = infer_role(
            ColumnFacts(
                name=f["name"], type=f["type"], rows=rows, non_null=f["non_null"], distinct=f["distinct"],
                avg_len=f.get("avg_len"), min=num["min"] if num else None, max=num["max"] if num else None,
            )
        )  # fmt: skip
        time = None
        if "epoch" in f:
            spans[f["name"]] = f["epoch"]
            time = p.time_stats(f["name"], f["type"], f["epoch"])
        top = (
            p.top_k(f["name"])
            if role in GROUP_ROLES | {ColumnRole.IDENTIFIER, ColumnRole.TEXT}
            else []
        )
        profiles.append(
            ColumnProfile(
                name=f["name"],
                type=f["type"],
                role=role,
                null_pct=_sig(f["null_pct"]) or 0.0,
                distinct=f["distinct"],
                numeric=NumericStats(**{k: _sig(v) for k, v in num.items()}) if num else None,
                top_k=top,
                time=time,
            )
        )

    by_name = {c.name: c for c in profiles}
    measures = [c.name for c in profiles if c.role in MEASURE_ROLES and c.distinct > 1]
    groups = [
        c.name
        for c in profiles
        if c.role in GROUP_ROLES | {ColumnRole.IDENTIFIER} and 1 < c.distinct < rows
    ]

    # Candidate keys: unique, non-null single columns, then pairs.
    key_roles = GROUP_ROLES | {ColumnRole.IDENTIFIER, ColumnRole.TIME}
    keys: list[list[str]] = [
        [c.name]
        for c in profiles
        if c.role in key_roles and c.distinct == rows and c.null_pct == 0 and rows > 1
    ]
    if not keys:
        pair_cols = [c.name for c in profiles if c.role in key_roles and c.distinct > 1][
            :MAX_PAIR_COLUMNS
        ]
        for a, b in itertools.combinations(pair_cols, 2):
            n = p.one(f"SELECT count(*) FROM (SELECT DISTINCT {quote(a)}, {quote(b)} FROM data)")[0]
            if n == rows:
                keys.append([a, b])
            if len(keys) >= 5:
                break

    # Hierarchies: parent → child where each child value has exactly one parent.
    hierarchies: list[list[str]] = []
    cats = [g for g in groups if by_name[g].role in GROUP_ROLES | {ColumnRole.IDENTIFIER}][
        :MAX_PAIR_COLUMNS
    ]
    for parent, child in itertools.permutations(cats, 2):
        if not by_name[parent].distinct < by_name[child].distinct:
            continue
        worst = p.one(
            f"SELECT max(n) FROM (SELECT {quote(child)}, count(DISTINCT {quote(parent)}) AS n FROM data "
            f"WHERE {quote(child)} IS NOT NULL GROUP BY 1)"
        )[0]
        if worst == 1:
            hierarchies.append([parent, child])

    correlations: list[Correlation] = []
    for a, b in itertools.combinations(measures[:15], 2):
        r = p.one(f"SELECT corr({quote(a)}, {quote(b)}) FROM data")[0]
        if r is not None and math.isfinite(r) and abs(r) >= 0.3:
            correlations.append(Correlation(a=a, b=b, r=max(-1.0, min(1.0, _sig(r, 3) or 0.0))))

    outliers: dict[str, int] = {}
    for m in measures:
        q = quote(m)
        n = p.one(
            f"WITH q AS (SELECT quantile_cont({q}, 0.25) AS q1, quantile_cont({q}, 0.75) AS q3 FROM data) "
            f"SELECT count(*) FROM data, q WHERE {q} < q1 - 1.5 * (q3 - q1) OR {q} > q3 + 1.5 * (q3 - q1)"
        )[0]
        if n:
            outliers[m] = int(n)

    trends: list[Trend] = []
    change_points: list[ChangePoint] = []
    times = sorted(spans, key=lambda t: -by_name[t].distinct)
    if times:
        tcol = times[0]
        lo, hi = spans[tcol]
        for m in measures:
            series = _series(p, tcol, by_name[tcol].type, m, spans[tcol])
            if len(series) < 3:
                continue
            tau = mann_kendall_tau(series)
            trends.append(
                Trend(measure=m, strength=_sig(abs(tau), 3) or 0.0, direction=int(np.sign(tau)))
            )
            split = best_split(series)
            if split and split[1] > 1.0:
                at = lo + (hi - lo) * split[0] / len(series)
                change_points.append(
                    ChangePoint(measure=m, at=datetime.fromtimestamp(at, UTC).isoformat())
                )

    grain = {
        DatasetKind.TEST_RESULTS: "one row per test case execution",
        DatasetKind.PERF_SAMPLES: "one row per request sample",
    }.get(kind) or (f"one row per {' + '.join(keys[0])}" if keys else "one row per event")

    profile = DatasetProfile(
        dataset_version_id=version_id,
        kind=kind,
        row_count=rows,
        columns=profiles,
        grain=grain,
        candidate_keys=keys,
        hierarchies=hierarchies,
        correlations=correlations,
        outliers=outliers,
        trends=trends,
        change_points=change_points,
    )
    domain, confidence = classify(profile)
    return profile.model_copy(update={"domain": domain, "domain_confidence": confidence})
