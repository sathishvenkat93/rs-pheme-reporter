"""R4 scoring: S = Σ wᵢ·termᵢ - Redundancy - Clutter (weights in config)."""

from __future__ import annotations

from collections.abc import Mapping

from app.models.spec import WidgetType
from app.recommend.rules import Candidate

# Cleveland-McGill: position > length > angle > area > colour.
PERCEPTION: dict[WidgetType, float] = {
    WidgetType.LINE: 1.0,
    WidgetType.KPI: 0.9,
    WidgetType.BAR: 0.9,
    WidgetType.SCATTER: 0.85,
    WidgetType.PARETO: 0.85,
    WidgetType.HISTOGRAM: 0.85,
    WidgetType.BOX: 0.8,
    WidgetType.STACKED_BAR: 0.7,
    WidgetType.AREA: 0.65,
    WidgetType.DONUT: 0.5,
    WidgetType.HEATMAP: 0.45,
    WidgetType.TABLE: 0.35,
}
REDUNDANCY_SCALE = 0.25


def clutter(c: Candidate) -> float:
    q = c.option.query
    penalty = 0.0
    if c.series > 7:
        penalty += 0.1
    elif q.top_n is not None and len(q.dimensions) > 1:
        penalty += 0.03
    categorical = (
        WidgetType.BAR,
        WidgetType.PARETO,
        WidgetType.DONUT,
        WidgetType.HEATMAP,
        WidgetType.BOX,
    )
    if c.categories > 20 and c.option.chart_type in categorical:
        penalty += 0.08
    if c.option.chart_type == WidgetType.HEATMAP and c.categories * c.series > 400:
        penalty += 0.1
    if c.option.chart_type in (WidgetType.BAR, WidgetType.LINE) and c.points > 500:
        penalty += 0.05
    return penalty


def similarity(a: Candidate, b: Candidate) -> float:
    qa, qb = a.option.query, b.option.query
    same_measures = set(qa.measures) == set(qb.measures)
    share_measure = bool(set(qa.measures) & set(qb.measures))
    same_dims = set(qa.dimensions) == set(qb.dimensions)
    if same_measures and same_dims:
        return 1.0
    if same_dims and share_measure:  # e.g. p95 over time vs p50/p90/p95 over time
        return 0.7
    if share_measure:  # the same number, another breakdown
        return 0.4
    if same_dims and not qa.dimensions:  # KPI tiles compete for one row
        return 0.3
    if same_dims:  # another number on the same axis: a complementary view
        return 0.1
    return 0.0


def base_score(c: Candidate, intent: float, w: Mapping[str, float]) -> dict[str, float]:
    terms = {
        "data_fit": round(min(1.0, c.data_fit), 4),
        "intent": intent,
        "insight": round(min(1.0, c.insight), 4),
        "perception": PERCEPTION[c.option.chart_type],
        "domain_prior": c.prior,
        "clutter": round(clutter(c) * w.get("clutter", 1.0), 4),
    }
    terms["base"] = round(
        sum(
            w.get(k, 0.0) * terms[k]
            for k in ("data_fit", "intent", "insight", "perception", "domain_prior")
        )
        - terms["clutter"],
        4,
    )
    return terms
