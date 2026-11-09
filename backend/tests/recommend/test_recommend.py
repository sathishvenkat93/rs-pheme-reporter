from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.models.chart import ChartRequest, FieldPhrase, FieldRoleHint, OptionStatus
from app.models.dataset import (
    ColumnProfile,
    ColumnRole,
    ColumnType,
    DatasetKind,
    DatasetProfile,
    NumericStats,
    TimeStats,
    TopValue,
)
from app.models.spec import WidgetType
from app.profiling.profiler import profile_paths
from app.query.compiler import compile_query
from app.query.executor import default_time_field, run_compiled
from app.recommend.context import context_for, violations
from app.recommend.engine import recommend

from ..profiling_helpers import fixture_parquet

FIXTURE_SETS = {
    "junit": [f"junit-build-{b}.xml" for b in range(101, 106)],
    "jtl": ["jmeter-load.jtl"],
    "daily": ["daily-metrics.csv"],
    "deploy": ["deployments.json"],
    "events": ["events.ndjson"],
    "inventory": ["inventory.xlsx"],
}


@pytest.fixture(scope="module")
def fixtures(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[list[Path], DatasetProfile]]:
    out = {}
    for key, names in FIXTURE_SETS.items():
        paths, kind = fixture_parquet(names, tmp_path_factory.mktemp(key))
        out[key] = (paths, profile_paths(paths, kind, uuid.uuid4()))
    return out


def top_ids(profile: DatasetProfile, n: int = 10) -> list[str]:
    return [o.id for o in recommend([profile])[:n]]


def test_junit_top_10(fixtures: dict[str, Any]) -> None:
    top = top_ids(fixtures["junit"][1])
    assert "kpi_pass_rate" in top
    assert "sig_pass_rate_by_build" in top
    assert "sig_failure_pareto" in top
    assert "sig_duration_by_suite" in top


def test_jtl_top_10(fixtures: dict[str, Any]) -> None:
    top = top_ids(fixtures["jtl"][1])
    assert "sig_p95_over_time" in top
    assert "sig_latency_histogram" in top
    assert "sig_throughput_over_time" in top
    assert "sig_errors_by_label" in top


def test_options_carry_scores_and_facts(fixtures: dict[str, Any]) -> None:
    recs = recommend([fixtures["junit"][1]])
    assert len(recs) == 25
    assert [o.status for o in recs[:10]] == [OptionStatus.RECOMMENDED] * 10
    totals = [o.score.total for o in recs if o.score]
    assert len(totals) == 25
    assert all(o.score is not None and o.score.facts for o in recs)
    assert len({o.id for o in recs}) == 25


@pytest.mark.parametrize("key", sorted(FIXTURE_SETS))
def test_every_candidate_query_compiles_and_runs(fixtures: dict[str, Any], key: str) -> None:
    paths, profile = fixtures[key]
    columns = {c.name: c.type for c in profile.columns}
    recs = recommend([profile])
    assert recs
    for o in recs:
        compiled = compile_query(
            o.query, columns, {m.id: m for m in o.measures}, time_field=default_time_field(columns)
        )
        rows, _ = run_compiled(paths, compiled, timeout_s=30, row_cap=10_000)
        assert rows, f"{o.id} returned no rows"


@pytest.mark.parametrize("key", sorted(FIXTURE_SETS))
def test_fixture_candidates_respect_guards(fixtures: dict[str, Any], key: str) -> None:
    profile = fixtures[key][1]
    ctx = context_for(profile)
    for o in recommend([profile]):
        assert violations(o, ctx) == [], o.id


@pytest.mark.parametrize(
    ("key", "request_"),
    [
        ("junit", ChartRequest(id="r1", raw_text="Average duration by suite as a bar", chart_type="bar",
                               field_phrases=[FieldPhrase(text="suite", role=FieldRoleHint.X),
                                              FieldPhrase(text="duration_ms", role=FieldRoleHint.Y)])),
        ("jtl", ChartRequest(id="r2", raw_text="Bytes by response code", chart_type="column",
                             field_phrases=[FieldPhrase(text="response_code", role=FieldRoleHint.X),
                                            FieldPhrase(text="bytes", role=FieldRoleHint.Y)])),
        ("daily", ChartRequest(id="r3", raw_text="Orders over time",
                               field_phrases=[FieldPhrase(text="date", role=FieldRoleHint.X),
                                              FieldPhrase(text="total_orders", role=FieldRoleHint.MEASURE)])),
    ],
)  # fmt: skip
def test_requested_valid_chart_ranks_in_top_5(
    fixtures: dict[str, Any], key: str, request_: ChartRequest
) -> None:
    recs = recommend([fixtures[key][1]], requests=[request_])
    top5 = recs[:5]
    requested = [o for o in top5 if o.request_id == request_.id]
    assert requested, [o.id for o in top5]
    assert requested[0].status is OptionStatus.REQUESTED
    assert requested[0].score is not None
    assert requested[0].score.intent == 1.0


def test_unsupported_requests_are_left_to_feasibility(fixtures: dict[str, Any]) -> None:
    req = ChartRequest(id="r9", raw_text="3D globe of traffic", chart_type="3d globe")
    recs = recommend([fixtures["daily"][1]], requests=[req])
    assert all(o.request_id != "r9" for o in recs)


# ----------------------------------------------------------------------------- property

NAMES = {
    ColumnRole.TIME: ["timestamp", "date", "created_at"],
    ColumnRole.STATUS: ["status", "result", "success"],
    ColumnRole.CATEGORY: ["suite", "label", "region", "case", "build", "service", "env", "team"],
    ColumnRole.IDENTIFIER: ["run_id", "user_id"],
    ColumnRole.DURATION: ["duration_ms", "latency_ms"],
    ColumnRole.COUNT: ["threads", "orders"],
    ColumnRole.MEASURE: ["bytes", "revenue", "score"],
    ColumnRole.RATIO: ["error_rate"],
}


@st.composite
def profiles(draw: st.DrawFn) -> DatasetProfile:
    rows = draw(st.integers(1, 200_000))
    roles = draw(st.lists(st.sampled_from(list(NAMES)), min_size=1, max_size=8))
    used: set[str] = set()
    cols: list[ColumnProfile] = []
    for role in roles:
        name = draw(st.sampled_from(NAMES[role]))
        if name in used:
            continue
        used.add(name)
        distinct = draw(st.integers(1, max(1, rows)))
        if role in (ColumnRole.STATUS,):
            distinct = min(distinct, 6)
        ctype = {
            ColumnRole.TIME: ColumnType.TIMESTAMP, ColumnRole.DURATION: ColumnType.FLOAT,
            ColumnRole.COUNT: ColumnType.INTEGER, ColumnRole.MEASURE: ColumnType.FLOAT, ColumnRole.RATIO: ColumnType.FLOAT,
        }.get(role, ColumnType.STRING)  # fmt: skip
        time = None
        if role == ColumnRole.TIME:
            span = draw(st.floats(0, 10**8))
            time = TimeStats(
                span_seconds=span,
                median_gap_seconds=draw(st.floats(0, max(1.0, span))),
                monotonic=True,
            )
        numeric = None
        if ctype in (ColumnType.FLOAT, ColumnType.INTEGER):
            numeric = NumericStats(
                min=0, max=100, mean=50, p50=50, p95=95, skew=draw(st.floats(-5, 5))
            )
        top = (
            [
                TopValue(value=f"v{i}", count=max(1, rows // (i + 2)))
                for i in range(min(distinct, 10))
            ]
            if ctype == ColumnType.STRING
            else []
        )
        cols.append(ColumnProfile(name=name, type=ctype, role=role, null_pct=0, distinct=distinct,
                                  numeric=numeric, top_k=top, time=time))  # fmt: skip
    domain = draw(st.sampled_from(["generic", "test_execution", "performance"]))
    return DatasetProfile(dataset_version_id=uuid.uuid4(), kind=DatasetKind.GENERIC, row_count=rows,
                          columns=cols, domain=domain, domain_confidence=0.8)  # fmt: skip


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(profiles())
def test_no_candidate_ever_violates_a_guard_rule(profile: DatasetProfile) -> None:
    ctx = context_for(profile)
    columns = {c.name: c.type for c in profile.columns}
    for o in recommend([profile]):
        assert violations(o, ctx) == [], (o.id, violations(o, ctx))
        assert o.chart_type in WidgetType
        compile_query(
            o.query, columns, {m.id: m for m in o.measures}, time_field=default_time_field(columns)
        )
