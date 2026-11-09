"""check(request, profile) → Verdict, running the R5 checks in order.

Adjusting checks change a working copy of the request and carry on; a not_feasible or
needs_clarification result stops. Reasons are short and plain, for non-technical readers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.feasibility.resolver import FieldResolver, Resolution
from app.models.chart import ChartRequest, FieldPhrase, FieldRoleHint, OptionStatus, VisualOption
from app.models.dataset import ColumnRole, DatasetProfile
from app.models.query import TimeGrain
from app.models.spec import WidgetType
from app.models.verdict import AppliedChange, Verdict, VerdictKind
from app.profiling.roles import MEASURE_ROLES
from app.query.compiler import GRAIN_SECONDS
from app.recommend.context import (
    MAX_CATEGORIES,
    MAX_DONUT_PARTS,
    MAX_HEATMAP_SIDE,
    MAX_SCATTER_POINTS,
    MAX_SERIES,
    Context,
    context_for,
    ident,
)
from app.recommend.rules import Candidate, make

SCATTER_SAMPLE = MAX_SCATTER_POINTS
DONUT_FALLBACK_TOP = 10

# Closest supported widget for chart types Pheme can't draw.
RENDERER_FALLBACKS: tuple[tuple[str, WidgetType], ...] = (
    (r"globe|map|choropleth|geo", WidgetType.BAR),
    (r"sankey|flow|chord|network", WidgetType.HEATMAP),
    (r"treemap|sunburst|icicle", WidgetType.STACKED_BAR),
    (r"gantt|timeline", WidgetType.TABLE),
    (r"violin|strip|beeswarm|ridge", WidgetType.BOX),
    (r"hexbin|density|contour", WidgetType.HEATMAP),
    (r"bubble", WidgetType.SCATTER),
    (r"waterfall|radar|spider|funnel|lollipop", WidgetType.BAR),
    (r"gauge|bullet|speedometer", WidgetType.KPI),
)

_GRAIN_WORDS: dict[str, TimeGrain] = {
    "minute": TimeGrain.MINUTE, "minutely": TimeGrain.MINUTE, "per minute": TimeGrain.MINUTE,
    "hour": TimeGrain.HOUR, "hourly": TimeGrain.HOUR, "per hour": TimeGrain.HOUR,
    "day": TimeGrain.DAY, "daily": TimeGrain.DAY, "per day": TimeGrain.DAY,
    "week": TimeGrain.WEEK, "weekly": TimeGrain.WEEK, "per week": TimeGrain.WEEK,
    "build": TimeGrain.BUILD, "per build": TimeGrain.BUILD,
}  # fmt: skip
_GRAIN_LABEL = {
    TimeGrain.MINUTE: "minute",
    TimeGrain.HOUR: "hourly",
    TimeGrain.DAY: "daily",
    TimeGrain.WEEK: "weekly",
}


@dataclass
class CheckResult:
    verdict: Verdict
    candidate: Candidate | None = None
    buildable_as_asked: bool = True

    @property
    def option(self) -> VisualOption | None:
        return self.candidate.option if self.candidate else None


@dataclass
class _Working:
    chart: WidgetType | None
    dims: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    grain: TimeGrain | None = None
    top_n: int | None = None
    limit: int | None = None
    changes: list[str] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    buildable: bool = True


def _human(name: str) -> str:
    return name.replace("_", " ")


def _stop(req: ChartRequest, kind: VerdictKind, check: str, reason: str, **kw: Any) -> CheckResult:
    return CheckResult(
        Verdict(kind=kind, request_id=req.id, check=check, reasons=[reason], **kw), None, False
    )


def _grain_of(text: str | None) -> TimeGrain | None:
    if not text:
        return None
    t = text.lower().strip()
    return _GRAIN_WORDS.get(t) or next((g for w, g in _GRAIN_WORDS.items() if w in t), None)


def _fallback(chart_type: str) -> WidgetType:
    t = chart_type.lower()
    return next((w for rx, w in RENDERER_FALLBACKS if re.search(rx, t)), WidgetType.BAR)


def _alt(req: ChartRequest, chart: str, phrases: list[FieldPhrase], text: str) -> ChartRequest:
    return ChartRequest(id=f"{req.id}_alt", raw_text=text, chart_type=chart, field_phrases=phrases,
                        time_grain=req.time_grain, top_n=req.top_n, source="agent")  # fmt: skip


def check_request(request: ChartRequest, ctx: Context) -> CheckResult:
    resolver = FieldResolver(ctx)
    chart = request.widget_type()
    w = _Working(chart=chart, grain=_grain_of(request.time_grain), top_n=request.top_n)
    derived: list[Resolution] = []

    # 1. Field exists (ambiguity surfaces here too: a vague word has several candidates).
    for phrase in request.field_phrases:
        r = resolver.resolve(phrase.text)
        if r.kind == "ambiguous":
            opts = r.candidates
            asked = " or ".join([", ".join(opts[:-1]), opts[-1]]) if len(opts) > 1 else opts[0]
            return _stop(request, VerdictKind.NEEDS_CLARIFICATION, "ambiguity",
                         f"\"{phrase.text}\" could mean {asked}. Which one should I show?",
                         candidates=r.candidate_targets)  # fmt: skip
        if r.kind == "missing":
            return _stop(request, VerdictKind.NOT_FEASIBLE, "field_exists",
                         f"This data has nothing about {phrase.text}. You can drop this chart or upload data that has it.")  # fmt: skip
        if r.kind == "derived":
            assert r.derived is not None
            if r.derived.kind == "field":
                return _stop(request, VerdictKind.NOT_FEASIBLE, "field_exists",
                             f"This data has no {r.derived.label}. You can drop this chart or upload data that includes it.")  # fmt: skip
            derived.append(r)
            continue
        assert r.target is not None
        if r.kind == "measure":
            w.measures.append(r.target)
            continue
        col = ctx.cols[r.target]
        wants_value = phrase.role in (FieldRoleHint.Y, FieldRoleHint.MEASURE)
        if (
            col.role in MEASURE_ROLES
            and (wants_value or (w.dims and phrase.role is None))
            and chart not in (WidgetType.HISTOGRAM, WidgetType.SCATTER, WidgetType.BOX)
        ):
            w.measures.append(
                ctx.add(ctx.catalog[ident("avg", col.name)])
                if ident("avg", col.name) in ctx.catalog
                else "rows"
            )
        else:
            w.dims.append(r.target)

    # 6 (early exit for derived metrics: they are judged by the Derivation check below)
    # 2. Type fit (only for chart types Pheme can draw; others go to the Renderer check).
    if chart is not None and not derived:
        if chart in (WidgetType.LINE, WidgetType.AREA):
            axes = [
                d for d in w.dims if ctx.is_time_dim(d, TimeGrain.BUILD if d == "build" else None)
            ]
            if not axes and w.dims:
                d = w.dims[0]
                return _stop(
                    request, VerdictKind.NOT_FEASIBLE, "type_fit",
                    f"A line needs something in order, like dates or builds. {_human(d).capitalize()} has no order. A bar chart of runs per {_human(d)} works.",
                    alternatives=[_alt(request, "bar", [FieldPhrase(text=d, role=FieldRoleHint.X)], f"Bar chart of runs per {_human(d)}")],
                )  # fmt: skip
            if not w.dims:
                axis = ctx.time_axes()
                if not axis:
                    return _stop(request, VerdictKind.NOT_FEASIBLE, "type_fit",
                                 "A trend needs dates, times or builds, and this data has none. A bar chart works instead.",
                                 alternatives=[_alt(request, "bar", request.field_phrases, "Bar chart instead")])  # fmt: skip
                w.dims.insert(
                    0, axis[-1].field if w.grain not in (None, TimeGrain.BUILD) else axis[0].field
                )
            elif axes and w.dims[0] != axes[0]:
                w.dims.remove(axes[0])
                w.dims.insert(0, axes[0])
        if chart in (WidgetType.HISTOGRAM, WidgetType.BOX):
            numeric = [d for d in w.dims if ctx.cols[d].role in MEASURE_ROLES]
            if not numeric:
                first = w.dims[0] if w.dims else None
                text = f"A {chart.value} needs numbers to spread out" + (
                    f", and {_human(first)} isn't a number." if first else "."
                )
                return _stop(request, VerdictKind.NOT_FEASIBLE, "type_fit", text + " A bar chart of counts works.",
                             alternatives=[_alt(request, "bar", request.field_phrases, "Bar chart of counts")])  # fmt: skip
        if (
            chart == WidgetType.SCATTER
            and len([d for d in w.dims if ctx.cols[d].role in MEASURE_ROLES]) < 2
        ):
            return _stop(request, VerdictKind.NOT_FEASIBLE, "type_fit",
                         "A scatter plot compares two numbers, and this request names fewer than two.",
                         alternatives=[_alt(request, "bar", request.field_phrases, "Bar chart instead")])  # fmt: skip
        if (
            chart in (WidgetType.DONUT, WidgetType.BAR, WidgetType.PARETO, WidgetType.STACKED_BAR)
            and not w.dims
        ):
            chart = WidgetType.KPI
            w.chart = chart

    # 3. Granularity.
    if w.dims and w.grain in GRAIN_SECONDS:
        col = ctx.cols[w.dims[0]]
        gap = col.time.median_gap_seconds if col.time else None
        if gap and GRAIN_SECONDS[w.grain] < gap * 0.99:
            coarser = next(
                (g for g in GRAIN_SECONDS if GRAIN_SECONDS[g] >= gap * 0.99), TimeGrain.WEEK
            )
            w.changes.append(
                f"The data is {_GRAIN_LABEL[coarser]}, so a {_GRAIN_LABEL[w.grain]} trend isn't possible. "
                f"Showing a {_GRAIN_LABEL[coarser]} trend instead."
            )
            w.before["time_grain"], w.after["time_grain"] = w.grain.value, coarser.value
            w.grain = coarser
            w.buildable = False
    if w.dims and w.grain is None:
        col = ctx.cols[w.dims[0]]
        if col.time is not None:
            w.grain = ctx.auto_grain(col)[0]
        elif w.dims[0] == "build" and ctx.is_time_dim("build", TimeGrain.BUILD):
            w.grain = TimeGrain.BUILD

    # 4. Cardinality.
    if w.chart is not None and w.dims:
        d0 = w.dims[0]
        n = ctx.distinct(d0)
        if w.chart == WidgetType.DONUT and n > MAX_DONUT_PARTS:
            top = DONUT_FALLBACK_TOP if n > DONUT_FALLBACK_TOP else None
            w.changes.append(
                f"{n} {_human(d0)} values are too many slices for a pie. "
                + (
                    f"Showing the top {top} as bars, with the rest as Other."
                    if top
                    else "Showing them as bars."
                )
            )
            w.before |= {"chart_type": request.chart_type or "donut"}
            w.after |= {"chart_type": "bar"} | ({"top_n": top} if top else {})
            w.chart, w.top_n = WidgetType.BAR, top or w.top_n
        elif (
            w.chart in (WidgetType.BAR, WidgetType.PARETO)
            and not ctx.is_time_dim(d0, w.grain)
            and n > MAX_CATEGORIES
            and not w.top_n
        ):
            w.changes.append(
                f"{n} {_human(d0)} values are too many bars to read. Showing the top {MAX_CATEGORIES}, with the rest as Other."
            )
            w.before["top_n"], w.after["top_n"] = None, MAX_CATEGORIES
            w.top_n = MAX_CATEGORIES
        elif (
            w.chart in (WidgetType.LINE, WidgetType.AREA)
            and len(w.dims) > 1
            and ctx.distinct(w.dims[1]) > MAX_SERIES
            and not w.top_n
        ):
            w.changes.append(
                f"{ctx.distinct(w.dims[1])} lines would be unreadable. Showing the top {MAX_SERIES}, with the rest as Other."
            )
            w.before["top_n"], w.after["top_n"] = None, MAX_SERIES
            w.top_n = MAX_SERIES
        elif (
            w.chart == WidgetType.HEATMAP
            and any(ctx.distinct(d) > MAX_HEATMAP_SIDE for d in w.dims)
            and not w.top_n
        ):
            w.changes.append(
                f"The grid would be too large. Showing the top {MAX_HEATMAP_SIDE} rows."
            )
            w.before["top_n"], w.after["top_n"] = None, MAX_HEATMAP_SIDE
            w.top_n = MAX_HEATMAP_SIDE

    # 5. Volume.
    if w.chart == WidgetType.SCATTER and ctx.rows > MAX_SCATTER_POINTS:
        w.changes.append(
            f"{ctx.rows:,} points are too many to draw. Showing a sample of {SCATTER_SAMPLE:,} points."
        )
        w.before["points"], w.after["points"] = ctx.rows, SCATTER_SAMPLE
        w.limit = SCATTER_SAMPLE
        w.buildable = False

    # 6. Derivation.
    for r in derived:
        assert r.derived is not None
        needs = " and ".join(_human(n) for n in r.derived.needs)
        missing = [n for n in r.derived.needs if n not in ctx.cols]
        reason = (
            f"{r.derived.label.capitalize()} needs {needs}, which this data doesn't have."
            if missing
            else f"Pheme can't work out {r.derived.label} from this data yet."
        )
        return _stop(request, VerdictKind.NOT_FEASIBLE, "derivation", reason)

    # 7. Renderer support.
    if chart is None and request.chart_type:
        fallback = _fallback(request.chart_type)
        phrases = list(request.field_phrases)
        group = next((c.name for c in ctx.group_columns if c.role == ColumnRole.GEO), None) or next(
            (c.name for c in ctx.group_columns), None
        )
        if fallback in (WidgetType.BAR, WidgetType.STACKED_BAR) and not w.dims and group:
            phrases.append(FieldPhrase(text=group, role=FieldRoleHint.X))
        by = f" by {_human(group)}" if group and fallback == WidgetType.BAR and not w.dims else ""
        name = {
            WidgetType.BAR: "bar chart",
            WidgetType.STACKED_BAR: "stacked bar chart",
            WidgetType.KPI: "number tile",
        }.get(fallback, fallback.value)
        return _stop(request, VerdictKind.NOT_FEASIBLE, "renderer",
                     f"Pheme can't draw a {request.chart_type}. A {name}{by} shows the same numbers.",
                     alternatives=[_alt(request, fallback.value, phrases, f"{name.capitalize()}{by}")])  # fmt: skip

    # 8. Ambiguity: nothing to go on at all.
    if not w.dims and not w.measures:
        kpis = [k.id for k in ctx.pack.kpis if k.id in ctx.pack_alias][:3] or ["rows"]
        return _stop(request, VerdictKind.NEEDS_CLARIFICATION, "ambiguity",
                     "Which numbers should this chart show?", candidates=kpis)  # fmt: skip

    final_chart = w.chart or (
        WidgetType.LINE
        if w.dims and ctx.is_time_dim(w.dims[0], w.grain)
        else WidgetType.BAR
        if w.dims
        else WidgetType.KPI
    )
    if not w.measures and final_chart not in (WidgetType.TABLE, WidgetType.SCATTER):
        w.measures = ["rows"]
    if final_chart == WidgetType.BOX:
        num = next(d for d in w.dims if ctx.cols[d].role in MEASURE_ROLES)
        w.dims.remove(num)
        w.measures = ctx.distribution_measures(num)
    candidate = make(
        ctx, "requested", final_chart, request.raw_text, dims=w.dims, measures=w.measures, grain=w.grain,
        top_n=w.top_n, limit=w.limit, data_fit=0.9, insight=0.5, facts=["requested"],
        option_id=ident("req", request.id),
    )  # fmt: skip
    if w.changes:
        status = OptionStatus.NEEDS_APPROVAL
        verdict = Verdict(
            kind=VerdictKind.ADJUSTED, request_id=request.id, check="adjusted", reasons=w.changes,
            applied_change=AppliedChange(description=" ".join(w.changes), before=w.before, after=w.after),
        )  # fmt: skip
    else:
        status = OptionStatus.REQUESTED
        verdict = Verdict(kind=VerdictKind.FEASIBLE, request_id=request.id)
    candidate.option = candidate.option.model_copy(
        update={"status": status, "request_id": request.id}
    )
    return CheckResult(verdict, candidate, w.buildable)


def check(
    request: ChartRequest, profile: DatasetProfile, *, targets: dict[str, float] | None = None
) -> CheckResult:
    return check_request(request, context_for(profile, targets=targets))


def feasibility_resolver(request: ChartRequest, ctx: Context) -> Candidate | None:
    """``recommend()`` hook: requested charts that pass (or pass with adjustments)."""
    result = check_request(request, ctx)
    return (
        result.candidate
        if result.verdict.kind in (VerdictKind.FEASIBLE, VerdictKind.ADJUSTED)
        else None
    )
