"""Approved plan → DashboardSpec: stable widget ids, measures, bindings, auto-layout."""

from __future__ import annotations

from app.dashboards.layout import auto_layout
from app.db.tables import DatasetRow, DatasetVersionRow
from app.domain_packs.loader import load_pack
from app.models.chart import VisualOption
from app.models.dataset import DatasetKind
from app.models.plan import DraftPlan
from app.models.query import Measure
from app.models.spec import DashboardSpec, DatasetBinding, Layout, Provenance, Widget
from app.orchestrator.plans import included
from app.recommend.context import ident


class BuildError(ValueError):
    """The plan can't become a dashboard. The message is meant for users."""


def widget_id(option_id: str) -> str:
    return ident("w", option_id)


def to_widget(o: VisualOption, decision_ids: list[str], targets: dict[str, float]) -> Widget:
    target = next((targets[m] for m in o.query.measures if m in targets), None)
    return Widget(
        id=widget_id(o.id),
        type=o.chart_type,
        title=o.title,
        description=o.insight,
        query=o.query.model_copy(update={"dataset": "ds"}),
        encoding=o.encoding,
        options=o.options,
        target=target,
        thresholds=o.thresholds,
        annotations=o.annotations,
        provenance=Provenance(
            option_id=o.id, rationale=o.rationale or None, decision_ids=decision_ids
        ),
    )


def build_spec(
    plan: DraftPlan,
    dataset: DatasetRow,
    version: DatasetVersionRow,
    *,
    dashboard_id: str,
    domain: str | None,
    targets: dict[str, float] | None = None,
) -> DashboardSpec:
    chosen = [o for o in plan.options if o.id in set(included(plan))]
    if not chosen:
        raise BuildError("The plan has no charts to build.")
    measures: dict[str, Measure] = {m.id: m for m in plan.measures}
    for o in chosen:
        for m in o.measures:
            prev = measures.get(m.id)
            if prev is not None and prev.expr != m.expr:
                raise BuildError(f"Two charts define the measure {m.id!r} differently.")
            measures[m.id] = m
    decisions = {d.item: d.id for d in plan.decisions}
    widgets = [
        to_widget(o, [decisions[o.id]] if o.id in decisions else [], targets or {}) for o in chosen
    ]
    pack = load_pack(domain or "generic")
    return DashboardSpec(
        dashboard_id=dashboard_id,
        title=f"{dataset.name} - {pack.label}"[:200],
        datasets=[
            DatasetBinding(
                id="ds",
                dataset_id=dataset.id,
                kind=DatasetKind(dataset.kind),
                schema_hash=version.schema_hash,
                mode="live",
            )
        ],
        filters=plan.filters,
        measures=list(measures.values()),
        layout=Layout(items=auto_layout(widgets, plan.layout_hints)),
        widgets=widgets,
    )
