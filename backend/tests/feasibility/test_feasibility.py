from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.feasibility.checks import check, feasibility_resolver
from app.feasibility.decisions import DecisionError, add_decisions, can_build, decision_for, resolve
from app.models.chart import ChartRequest, FieldPhrase, FieldRoleHint, OptionStatus
from app.models.dataset import DatasetProfile
from app.models.plan import DecisionChoice, DraftPlan
from app.models.spec import WidgetType
from app.models.verdict import VerdictKind
from app.profiling.profiler import profile_paths
from app.recommend.engine import recommend

from ..profiling_helpers import fixture_parquet

X, Y, SPLIT, M = FieldRoleHint.X, FieldRoleHint.Y, FieldRoleHint.SPLIT, FieldRoleHint.MEASURE


@pytest.fixture(scope="module")
def profiles(tmp_path_factory: pytest.TempPathFactory) -> dict[str, DatasetProfile]:
    out = {}
    for key, names in {
        "junit": [f"junit-build-{b}.xml" for b in range(101, 106)],
        "jtl": ["jmeter-load.jtl"],
        "daily": ["daily-metrics.csv"],
    }.items():
        paths, kind = fixture_parquet(names, tmp_path_factory.mktemp(key))
        out[key] = profile_paths(paths, kind, uuid.uuid4())
    out["jtl_8m"] = out["jtl"].model_copy(
        update={"row_count": 8_000_000}
    )  # Volume row: synthetic size
    return out


def req(
    rid: str, text: str, chart: str | None, *phrases: tuple[str, FieldRoleHint | None], **kw: Any
) -> ChartRequest:
    return ChartRequest(
        id=rid,
        raw_text=text,
        chart_type=chart,
        field_phrases=[FieldPhrase(text=t, role=r) for t, r in phrases],
        **kw,
    )


@dataclass
class Case:
    check: str
    dataset: str
    request: ChartRequest
    verdict: VerdictKind
    alternative: str | None = None  # expected alternative chart type, or applied change key
    buildable: bool | None = None


R5 = [
    Case("field_exists", "jtl", req("cpu", "CPU usage over time", "line", ("cpu usage", Y), ("time", X)),
         VerdictKind.NOT_FEASIBLE),
    Case("type_fit", "junit", req("names", "Line chart of test names", "line", ("test names", X)),
         VerdictKind.NOT_FEASIBLE, alternative="bar"),
    Case("granularity", "daily", req("hourly", "Hourly trend of orders", "line", ("orders", Y), time_grain="hourly"),
         VerdictKind.ADJUSTED, alternative="time_grain", buildable=False),
    Case("cardinality", "junit", req("pie", "Pie of failures by test", "pie", ("failures", M), ("test", SPLIT)),
         VerdictKind.ADJUSTED, alternative="chart_type", buildable=True),
    Case("volume", "jtl_8m", req("scatter", "Scatter of latency vs bytes", "scatter", ("latency", X), ("bytes", Y)),
         VerdictKind.ADJUSTED, alternative="points", buildable=False),
    Case("derivation", "junit", req("mttr", "MTTR by week", "line", ("mttr", Y), time_grain="week"),
         VerdictKind.NOT_FEASIBLE),
    Case("renderer", "daily", req("globe", "3D globe of traffic", "3d globe", ("traffic", M)),
         VerdictKind.NOT_FEASIBLE, alternative="bar"),
    Case("ambiguity", "jtl", req("perf", "Show performance", None, ("performance", None)),
         VerdictKind.NEEDS_CLARIFICATION),
]  # fmt: skip


@pytest.mark.parametrize("case", R5, ids=[c.check for c in R5])
def test_r5_rows(case: Case, profiles: dict[str, DatasetProfile]) -> None:
    result = check(case.request, profiles[case.dataset])
    v = result.verdict
    assert v.kind is case.verdict, v
    if case.verdict in (VerdictKind.NOT_FEASIBLE, VerdictKind.NEEDS_CLARIFICATION):
        assert v.check == case.check
        assert result.option is None
    if case.verdict is VerdictKind.NOT_FEASIBLE and case.alternative:
        assert v.alternatives, "expected an alternative"
        assert ChartRequest.model_validate(v.alternatives[0]).widget_type() == WidgetType(
            case.alternative
        )
    if case.verdict is VerdictKind.ADJUSTED:
        assert v.applied_change is not None
        assert case.alternative in v.applied_change.after
        assert result.option is not None
        assert result.option.status is OptionStatus.NEEDS_APPROVAL
        assert result.buildable_as_asked is case.buildable
    if case.verdict is VerdictKind.NEEDS_CLARIFICATION:
        assert len(v.candidates) == 3


def test_specific_outcomes(profiles: dict[str, DatasetProfile]) -> None:
    pie = check(R5[3].request, profiles["junit"])
    assert pie.option is not None
    assert pie.option.chart_type is WidgetType.BAR
    assert pie.option.query.top_n == 10
    assert pie.verdict.applied_change is not None
    assert pie.verdict.applied_change.before == {"chart_type": "pie"}

    hourly = check(R5[2].request, profiles["daily"])
    assert hourly.option is not None
    assert hourly.option.query.time_grain == "day"

    names = check(R5[1].request, profiles["junit"])
    assert names.verdict.alternatives[0].raw_text == "Bar chart of runs per case"

    globe = check(R5[6].request, profiles["daily"])
    assert any(p.text == "region" for p in globe.verdict.alternatives[0].field_phrases)

    mttr = check(R5[5].request, profiles["junit"])
    assert "failed at" in mttr.verdict.reasons[0]

    perf = check(R5[7].request, profiles["jtl"])
    assert perf.verdict.candidates == ["p95", "throughput", "error_rate"]


def test_feasible_request_passes(profiles: dict[str, DatasetProfile]) -> None:
    result = check(
        req("ok", "p95 latency by endpoint", "bar", ("endpoint", X), ("p95", M)), profiles["jtl"]
    )
    assert result.verdict.kind is VerdictKind.FEASIBLE
    assert result.option is not None
    assert result.option.query.dimensions == ["label"]
    assert result.option.query.measures == ["p95"]


def test_alternatives_pass_their_own_check(profiles: dict[str, DatasetProfile]) -> None:
    for case in R5:
        v = check(case.request, profiles[case.dataset]).verdict
        for alt in v.alternatives:
            assert check(alt, profiles[case.dataset]).verdict.kind in (
                VerdictKind.FEASIBLE,
                VerdictKind.ADJUSTED,
            )


JARGON = re.compile(
    r"\b(dtype|varchar|int64|float64|string|integer|boolean|schema|column type|cardinality|granularity|"
    r"null|sql|timestamp|datatype|query|aggregation|enum|parquet)\b",
    re.IGNORECASE,
)


def test_reasons_are_short_and_plain(profiles: dict[str, DatasetProfile]) -> None:
    for case in R5:
        for reason in check(case.request, profiles[case.dataset]).verdict.reasons:
            assert len(reason.split()) < 25, reason
            assert not JARGON.search(reason), reason


# ----------------------------------------------------------------------------- decisions


def _plan(profiles: dict[str, DatasetProfile]) -> DraftPlan:
    options, requests, decisions = [], [], []
    for case in R5[:4]:  # not_feasible, not_feasible, adjusted, adjusted
        result = check(case.request, profiles[case.dataset])
        requests.append(case.request)
        if result.option is not None:
            options.append(result.option)
        item = result.option.id if result.option else case.request.id
        d = decision_for(result.verdict, item, result.buildable_as_asked)
        assert d is not None
        decisions.append(d)
    plan = DraftPlan(
        id=uuid.uuid4(), session_id=uuid.uuid4(), version=1, options=options, requests=requests
    )
    return add_decisions(plan, decisions)


def test_open_decisions_block_the_build_until_the_last_is_resolved(
    profiles: dict[str, DatasetProfile],
) -> None:
    plan = _plan(profiles)
    ok, open_ids = can_build(plan)
    assert not ok
    assert len(open_ids) == 4
    user = uuid.uuid4()
    choices = {
        "D_cpu": DecisionChoice.DROP,
        "D_names": DecisionChoice.ACCEPT_ALTERNATIVE,
        "D_req_hourly": DecisionChoice.ACCEPT_ALTERNATIVE,
        "D_req_pie": DecisionChoice.KEEP_AS_ASKED,
    }
    for i, (did, choice) in enumerate(choices.items()):
        assert can_build(plan)[0] is False
        plan = resolve(plan, did, choice, user)
        assert len(can_build(plan)[1]) == 3 - i
    assert can_build(plan) == (True, [])


def test_keep_as_asked_only_when_buildable(profiles: dict[str, DatasetProfile]) -> None:
    plan = _plan(profiles)
    hourly = next(d for d in plan.decisions if d.id == "D_req_hourly")
    assert DecisionChoice.KEEP_AS_ASKED not in hourly.options
    with pytest.raises(DecisionError, match="can't be built as asked"):
        resolve(plan, "D_req_hourly", DecisionChoice.KEEP_AS_ASKED, uuid.uuid4())
    pie = next(d for d in plan.decisions if d.id == "D_req_pie")
    assert DecisionChoice.KEEP_AS_ASKED in pie.options


def test_drop_and_provide_data_exclude_the_option(profiles: dict[str, DatasetProfile]) -> None:
    plan = _plan(profiles)
    plan = resolve(plan, "D_req_pie", DecisionChoice.DROP, uuid.uuid4())
    assert next(o for o in plan.options if o.id == "req_pie").status is OptionStatus.EXCLUDED
    plan = resolve(plan, "D_cpu", DecisionChoice.PROVIDE_DATA, uuid.uuid4())
    cpu = next(d for d in plan.decisions if d.id == "D_cpu")
    assert cpu.choice_detail == "waiting for data"
    with pytest.raises(DecisionError, match="already been made"):
        resolve(plan, "D_cpu", DecisionChoice.DROP, uuid.uuid4())


def test_clarification_needs_a_pick(profiles: dict[str, DatasetProfile]) -> None:
    result = check(R5[7].request, profiles["jtl"])
    d = decision_for(result.verdict, "perf", result.buildable_as_asked)
    assert d is not None
    assert d.options == [DecisionChoice.CHOOSE_CANDIDATE, DecisionChoice.DROP]
    plan = DraftPlan(
        id=uuid.uuid4(), session_id=uuid.uuid4(), version=1, requests=[R5[7].request], decisions=[d]
    )
    with pytest.raises(DecisionError, match="Pick one"):
        resolve(plan, d.id, DecisionChoice.CHOOSE_CANDIDATE, uuid.uuid4())
    plan = resolve(plan, d.id, DecisionChoice.CHOOSE_CANDIDATE, uuid.uuid4(), detail="p95")
    assert can_build(plan) == (True, [])


def test_recommend_pins_adjusted_requests_for_approval(profiles: dict[str, DatasetProfile]) -> None:
    pie = R5[3].request
    recs = recommend([profiles["junit"]], requests=[pie], resolver=feasibility_resolver)
    assert recs[0].request_id == "pie"
    assert recs[0].status is OptionStatus.NEEDS_APPROVAL
    infeasible = recommend(
        [profiles["junit"]], requests=[R5[1].request], resolver=feasibility_resolver
    )
    assert all(o.request_id != "names" for o in infeasible)
