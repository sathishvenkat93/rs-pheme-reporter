"""Every model survives model -> JSON -> model unchanged."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from pydantic import BaseModel

from app.models.chart import (
    ChartRequest,
    FieldPhrase,
    FieldRoleHint,
    OptionStatus,
    ScoreBreakdown,
    VisualOption,
)
from app.models.dataset import (
    ColumnProfile,
    ColumnRole,
    ColumnSchema,
    ColumnType,
    Correlation,
    Dataset,
    DatasetKind,
    DatasetProfile,
    DatasetVersion,
    NumericStats,
    TimeStats,
    TopValue,
    Trend,
    schema_hash,
)
from app.models.patch import WidgetPatchOp
from app.models.plan import (
    AddOp,
    AnnotateOp,
    ChangeEncodingOp,
    ChangeTypeOp,
    Decision,
    DecisionChoice,
    DeriveMetricOp,
    DraftPlan,
    FilterOp,
    LayoutOp,
    RemoveOp,
    StyleOp,
)
from app.models.query import (
    EqFilter,
    InFilter,
    LastNFilter,
    Measure,
    RangeFilter,
    RelativeTimeFilter,
    SemanticQuery,
    SortKey,
    TimeGrain,
)
from app.models.spec import Annotation, DashboardSpec, Encoding, Threshold, WidgetType
from app.models.verdict import AppliedChange, Verdict, VerdictKind

from .factories import r2_spec, specs

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
COLS = [
    ColumnSchema(name="build", type=ColumnType.STRING),
    ColumnSchema(name="duration_ms", type=ColumnType.FLOAT),
]
QUERY = SemanticQuery(
    dataset="ds_tests",
    dimensions=["build"],
    measures=["pass_rate"],
    filters=[
        EqFilter(field="suite", value="api"),
        InFilter(field="status", values=["failed", "error"]),
        RangeFilter(field="duration_ms", gte=0, lte=5000.5),
        RelativeTimeFilter(field="timestamp", last=7, unit="day"),
        LastNFilter(field="build", n=7),
    ],
    time_grain=TimeGrain.BUILD,
    sort=[SortKey(field="build", dir="desc")],
    top_n=10,
    limit=500,
)
PASS_RATE = Measure(
    id="pass_rate", expr="ratio(count_if(status == 'passed'), count())", format="percent"
)
OPTION = VisualOption(
    id="O2",
    title="Pass rate by build",
    chart_type=WidgetType.LINE,
    fields=["build", "status"],
    query=QUERY,
    measures=[PASS_RATE],
    encoding=Encoding(x="build", y="pass_rate"),
    rationale="Trend across 5 builds",
    score=ScoreBreakdown(
        data_fit=0.9,
        intent=0,
        insight=0.7,
        perception=0.8,
        domain_prior=1,
        redundancy=0,
        clutter=0.1,
        total=0.71,
        facts=["5 builds"],
    ),
    status=OptionStatus.RECOMMENDED,
)
REQUEST = ChartRequest(
    id="R1",
    raw_text="Show a pie of failures by test",
    chart_type="pie",
    field_phrases=[
        FieldPhrase(text="failures", role=FieldRoleHint.MEASURE),
        FieldPhrase(text="test", role=FieldRoleHint.SPLIT),
    ],
)

EXAMPLES: list[BaseModel] = [
    ColumnSchema(name="build", type=ColumnType.STRING),
    Dataset(
        id=uuid4(),
        workspace_id=uuid4(),
        name="junit",
        kind=DatasetKind.TEST_RESULTS,
        created_at=NOW,
    ),
    DatasetVersion(
        id=uuid4(),
        dataset_id=uuid4(),
        version=2,
        parts=["a.parquet", "b.parquet"],
        columns=COLS,
        schema_hash=schema_hash(COLS),
        row_count=10,
        upload_ids=[uuid4(), uuid4()],
        created_at=NOW,
    ),
    DatasetProfile(
        dataset_version_id=uuid4(),
        kind=DatasetKind.PERF_SAMPLES,
        row_count=2000,
        columns=[
            ColumnProfile(
                name="latency_ms",
                type=ColumnType.FLOAT,
                role=ColumnRole.DURATION,
                null_pct=0.5,
                distinct=812,
                numeric=NumericStats(min=3, max=4100, mean=230.4, p50=190, p95=780, skew=2.1),
                top_k=[TopValue(value="200", count=12)],
            ),
            ColumnProfile(
                name="timestamp",
                type=ColumnType.TIMESTAMP,
                role=ColumnRole.TIME,
                null_pct=0,
                distinct=2000,
                time=TimeStats(span_seconds=600, median_gap_seconds=0.3, monotonic=True),
            ),
        ],
        grain="sample",
        hierarchies=[["suite", "case"]],
        correlations=[Correlation(a="latency_ms", b="bytes", r=0.42)],
        outliers={"latency_ms": 14},
        trends=[Trend(measure="latency_ms", strength=0.3, direction=1)],
        domain="performance",
        domain_confidence=0.95,
    ),
    QUERY,
    PASS_RATE,
    REQUEST,
    OPTION,
    Verdict(
        kind=VerdictKind.ADJUSTED,
        request_id="R1",
        check="cardinality",
        reasons=["240 tests are too many slices for a pie."],
        alternatives=[REQUEST.model_copy(update={"chart_type": "bar", "top_n": 10})],
        applied_change=AppliedChange(
            description="pie → top-10 bar + Other",
            before={"type": "donut"},
            after={"type": "bar", "top_n": 10},
        ),
    ),
    Verdict(
        kind=VerdictKind.NEEDS_CLARIFICATION,
        request_id="R2",
        reasons=["Which performance measure?"],
        candidates=["latency_ms", "throughput", "errors"],
    ),
    Decision(
        id="D1",
        item="O2",
        reason="Changed pie to bar",
        options=list(DecisionChoice),
        choice=DecisionChoice.ACCEPT_ALTERNATIVE,
        decided_by=uuid4(),
        decided_at=NOW,
    ),
    DraftPlan(
        id=uuid4(),
        session_id=uuid4(),
        version=3,
        options=[OPTION],
        measures=[PASS_RATE],
        layout_hints=["kpis_top"],
        decisions=[Decision(id="D2", item="O2", reason="r", options=[DecisionChoice.DROP])],
    ),
    AddOp(option=OPTION),
    RemoveOp(option_id="O2"),
    ChangeTypeOp(option_id="O2", chart_type=WidgetType.BAR),
    ChangeEncodingOp(option_id="O2", dimensions=["build", "env"], encoding=Encoding(series="env")),
    FilterOp(filter=LastNFilter(field="build", n=7)),
    DeriveMetricOp(measure=Measure(id="p95", expr="percentile(latency_ms, 95)")),
    LayoutOp(hints=["kpis_top", "two_columns"]),
    StyleOp(option_id="O2", thresholds=[Threshold(below=0.9, color="critical")]),
    AnnotateOp(option_id="O2", annotation=Annotation(type="rule", x="3.2", label="3.2 release")),
    WidgetPatchOp(op="replace", path="/widgets/w_pass_kpi/title", value="Pass %"),
    DashboardSpec.model_validate_json(
        DashboardSpec.model_validate(r2_spec(), strict=False).model_dump_json()
    ),
]


@pytest.mark.parametrize("model", EXAMPLES, ids=lambda m: type(m).__name__)
def test_round_trip(model: BaseModel) -> None:
    again = type(model).model_validate_json(model.model_dump_json())
    assert again == model
    assert again.model_dump_json() == model.model_dump_json()


def test_r2_example_validates() -> None:
    spec = DashboardSpec.model_validate_json(
        DashboardSpec.model_validate(r2_spec(), strict=False).model_dump_json()
    )
    assert [w.id for w in spec.widgets] == ["w_pass_kpi", "w_pass_trend"]


@settings(max_examples=50)
@given(specs())
def test_generated_specs_round_trip(doc: dict[str, object]) -> None:
    import json

    spec = DashboardSpec.model_validate_json(json.dumps(doc))
    assert DashboardSpec.model_validate_json(spec.model_dump_json()) == spec
