"""Candidate rules: one per R3 row, plus the domain pack's signature visuals.

Rules obey the guard rules by construction (top N where cardinality is high, no columns
over long periods, …); ``context.violations`` is the independent check used in tests.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from app.models.chart import OptionStatus, VisualOption
from app.models.dataset import ColumnRole
from app.models.grammar import fields_used
from app.models.query import Filter, SemanticQuery, SortKey, TimeGrain
from app.models.spec import Encoding, WidgetOptions, WidgetType
from app.recommend.context import (
    MAX_BAR_PERIODS,
    MAX_CATEGORIES,
    MAX_DONUT_PARTS,
    MAX_HEATMAP_SIDE,
    MAX_SCATTER_POINTS,
    MAX_SERIES,
    MIN_DISTRIBUTION_ROWS,
    MIN_ROWS_PER_CATEGORY,
    MIN_TIME_POINTS,
    SMALL,
    Context,
    ident,
)


@dataclass
class Candidate:
    option: VisualOption
    rule: str
    data_fit: float
    insight: float
    prior: float = 0.0
    facts: list[str] = field(default_factory=list)
    series: int = 1
    categories: int = 0
    points: int = 0


def make(
    ctx: Context,
    rule: str,
    chart: WidgetType,
    title: str,
    *,
    dims: list[str] | None = None,
    measures: list[str] | None = None,
    grain: TimeGrain | None = None,
    top_n: int | None = None,
    sort: list[SortKey] | None = None,
    filters: list[Filter] | None = None,
    limit: int | None = None,
    encoding: Encoding | None = None,
    options: WidgetOptions | None = None,
    data_fit: float = 0.8,
    insight: float = 0.5,
    prior: float = 0.0,
    facts: list[str] | None = None,
    option_id: str | None = None,
) -> Candidate:
    dims = dims or []
    measures = measures or []
    defs = [ctx.catalog[m] for m in measures]
    used = sorted({*dims, *(f for d in defs for f in fields_used(d.ast()))})
    query = SemanticQuery(
        dataset=ctx.dataset,
        dimensions=dims,
        measures=measures,
        filters=filters or [],
        time_grain=grain,
        sort=sort or [],
        top_n=top_n,
        limit=limit,
    )
    option = VisualOption(
        id=option_id or ident(chart.value, *dims, *measures),
        title=title[:200],
        chart_type=chart,
        fields=used,
        query=query,
        measures=defs,
        encoding=encoding or _default_encoding(chart, dims, measures),
        options=options,
        status=OptionStatus.OPTIONAL,
    )
    series = (
        min(ctx.distinct(dims[1]), (top_n or 10**9) + 1) if len(dims) > 1 else max(1, len(measures))
    )
    points = ctx.points_for(dims[0], grain) if dims else 1
    if dims and ctx.is_time_dim(dims[0], grain):
        cats = points  # a time axis has buckets, not categories
    else:
        cats = min(ctx.distinct(dims[0]), (top_n or 10**9) + 1) if dims else 0
    return Candidate(
        option=option, rule=rule, data_fit=data_fit, insight=insight, prior=prior, facts=facts or [],
        series=series, categories=cats, points=points,
    )  # fmt: skip


def _default_encoding(chart: WidgetType, dims: list[str], measures: list[str]) -> Encoding:
    m = measures[0] if measures else None
    if chart == WidgetType.KPI:
        return Encoding(value=m)
    if chart == WidgetType.DONUT:
        return Encoding(color=dims[0] if dims else None, value=m)
    if chart == WidgetType.HEATMAP and len(dims) == 2:
        return Encoding(x=dims[1], y=dims[0], value=m)
    if chart == WidgetType.SCATTER:
        return Encoding(x=dims[0] if dims else None, y=dims[1] if len(dims) > 1 else None)
    if chart == WidgetType.TABLE:
        return Encoding()
    return Encoding(x=dims[0] if dims else None, y=m, series=dims[1] if len(dims) > 1 else None)


def _label(ctx: Context, measure_id: str) -> str:
    return ctx.catalog[measure_id].label or measure_id.replace("_", " ")


def _trend(ctx: Context, measure_id: str) -> tuple[float, list[str]]:
    fields = sorted(fields_used(ctx.catalog[measure_id].ast()))
    for t in ctx.profile.trends:
        if t.measure in fields:
            facts = [f"trend strength {t.strength:.2f} in {t.measure}"]
            cp = any(c.measure == t.measure for c in ctx.profile.change_points)
            if cp:
                facts.append(f"change point in {t.measure}")
            return min(1.0, 0.3 + t.strength + (0.2 if cp else 0)), facts
    return 0.5, []


def _imbalance(ctx: Context, dim: str, measure_id: str = "rows") -> float:
    """1 - normalised entropy of the dimension's top values: how uneven the parts are.

    Only evidence for additive measures (counts, sums); other measures get a neutral 0.4.
    """
    if not ctx.additive(measure_id):
        return 0.4
    top = ctx.cols[dim].top_k
    total = sum(t.count for t in top)
    if len(top) < 2 or total == 0:
        return 0.3
    h = -sum(t.count / total * math.log(t.count / total) for t in top if t.count)
    return round(max(0.2, 1 - h / math.log(len(top))), 3)


# ----------------------------------------------------------------------------- R3 rows

Rule = Callable[[Context], Iterator[Candidate]]


def r_kpi(ctx: Context) -> Iterator[Candidate]:
    """One measure, no dimension → KPI tile (target shown only when known)."""
    kpis = [ctx.pack_alias[k.id] for k in ctx.pack.kpis if k.id in ctx.pack_alias]
    generic = [m for m in ctx.headline_measures(3) if m not in kpis] if not kpis else []
    ordered = kpis + generic
    for rank, mid in enumerate(ordered):
        has_target = mid in ctx.targets
        priority = 0.05 * (len(ordered) - rank) / len(ordered)  # packs list KPIs in priority order
        yield make(
            ctx, "kpi", WidgetType.KPI, _label(ctx, mid), measures=[mid],
            data_fit=1.0, insight=(0.7 if has_target else 0.5) + priority, prior=1.0 if mid in ctx.pack_ids else 0.0,
            facts=["domain KPI" if mid in ctx.pack_ids else "headline measure"] + (["target known"] if has_target else []),
            option_id=ident("kpi", mid),
        )  # fmt: skip


def r_time_measure(ctx: Context) -> Iterator[Candidate]:
    """Time + measure → line (and columns up to ~30 periods)."""
    for axis in ctx.time_axes():
        if axis.points < MIN_TIME_POINTS:
            continue
        for mid in ctx.headline_measures(4):
            insight, facts = _trend(ctx, mid)
            facts = [
                f"{axis.points} {'builds' if axis.grain == TimeGrain.BUILD else 'time points'}",
                *facts,
            ]
            title = f"{_label(ctx, mid)} by {axis.field}"
            yield make(ctx, "time", WidgetType.LINE, title, dims=[axis.field], measures=[mid], grain=axis.grain,
                       data_fit=0.95, insight=insight, facts=facts)  # fmt: skip
            if axis.points <= MAX_BAR_PERIODS:
                yield make(ctx, "time", WidgetType.BAR, title, dims=[axis.field], measures=[mid], grain=axis.grain,
                           data_fit=0.8, insight=insight, facts=facts)  # fmt: skip


def r_time_category(ctx: Context) -> Iterator[Candidate]:
    """Time + measure + category → multi-line (top 7 + Other)."""
    for axis in ctx.time_axes()[:1]:
        if axis.points < MIN_TIME_POINTS:
            continue
        for c in ctx.group_columns[:3]:
            if c.name == axis.field:
                continue
            for mid in ctx.headline_measures(2):
                top = MAX_SERIES if c.distinct > MAX_SERIES else None
                yield make(
                    ctx, "time_split", WidgetType.LINE, f"{_label(ctx, mid)} by {axis.field} and {c.name}",
                    dims=[axis.field, c.name], measures=[mid], grain=axis.grain, top_n=top,
                    data_fit=0.85 if top is None else 0.75, insight=_trend(ctx, mid)[0] * 0.9,
                    facts=[f"{c.distinct} {c.name} values"],
                )  # fmt: skip


def r_category(ctx: Context) -> Iterator[Candidate]:
    """Category + measure → sorted bar (top 20), Pareto for additive measures."""
    for c in ctx.group_columns:
        top = MAX_CATEGORIES if c.distinct > MAX_CATEGORIES else None
        for mid in ctx.headline_measures(3):
            sort = [SortKey(field=mid, dir="desc")]
            fit = 0.9 if 2 <= c.distinct <= 12 else 0.75
            yield make(ctx, "category", WidgetType.BAR, f"{_label(ctx, mid)} by {c.name}", dims=[c.name],
                       measures=[mid], top_n=top, sort=sort, options=WidgetOptions(horizontal=c.distinct > 8),
                       data_fit=fit, insight=_imbalance(ctx, c.name, mid), facts=[f"{c.distinct} {c.name} values"])  # fmt: skip
            if ctx.additive(mid) and c.distinct >= 3:
                yield make(ctx, "category", WidgetType.PARETO, f"{_label(ctx, mid)} by {c.name} (Pareto)",
                           dims=[c.name], measures=[mid], top_n=top, sort=sort, data_fit=fit * 0.95,
                           insight=_imbalance(ctx, c.name, mid), facts=[f"{c.distinct} {c.name} values"])  # fmt: skip


def r_part_of_whole(ctx: Context) -> Iterator[Candidate]:
    """Part of a whole → donut (≤ 6 parts) or 100% stacked bar over time."""
    additive = [m for m in ctx.headline_measures(6) if ctx.additive(m)] or ["rows"]
    for c in ctx.group_columns:
        if 2 <= c.distinct <= MAX_DONUT_PARTS:
            yield make(ctx, "part", WidgetType.DONUT, f"Share of {_label(ctx, additive[0]).lower()} by {c.name}",
                       dims=[c.name], measures=[additive[0]], data_fit=0.85, insight=_imbalance(ctx, c.name, additive[0]),
                       facts=[f"{c.distinct} parts"])  # fmt: skip
    axes = ctx.time_axes()
    status = [
        c for c in ctx.group_columns if c.role == ColumnRole.STATUS and c.distinct <= MAX_SERIES
    ]
    if axes and axes[0].points >= MIN_TIME_POINTS and axes[0].points <= MAX_BAR_PERIODS and status:
        a, s = axes[0], status[0]
        yield make(ctx, "part", WidgetType.STACKED_BAR, f"{s.name} mix by {a.field}", dims=[a.field, s.name],
                   measures=["rows"], grain=a.grain, options=WidgetOptions(stack="percent"), data_fit=0.85,
                   insight=0.55, facts=[f"{s.distinct} {s.name} values over {a.points} points"])  # fmt: skip


def r_distribution(ctx: Context) -> Iterator[Candidate]:
    """One continuous measure → histogram (≥ 30 rows)."""
    if ctx.rows < MIN_DISTRIBUTION_ROWS:
        return
    for c in ctx.measure_columns[:4]:
        if c.role == ColumnRole.COUNT and c.distinct < 10:
            continue
        skew = abs(c.numeric.skew or 0) if c.numeric else 0
        outliers = ctx.profile.outliers.get(c.name, 0)
        insight = min(1.0, 0.3 + skew / 3 + (0.2 if outliers else 0))
        yield make(ctx, "distribution", WidgetType.HISTOGRAM, f"Distribution of {c.name}", dims=[c.name],
                   measures=["rows"], options=WidgetOptions(bins=30), data_fit=0.9, insight=insight,
                   facts=[f"skew {skew:.2f}", f"{outliers} outliers"])  # fmt: skip


def r_spread(ctx: Context) -> Iterator[Candidate]:
    """Measure spread by category → box plot (≥ 30 rows, ≥ 5 rows per category)."""
    if ctx.rows < MIN_DISTRIBUTION_ROWS:
        return
    for c in ctx.group_columns:
        if c.distinct > MAX_HEATMAP_SIDE or ctx.rows / c.distinct < MIN_ROWS_PER_CATEGORY:
            continue
        for m in ctx.measure_columns[:2]:
            ids = ctx.distribution_measures(m.name)
            yield make(ctx, "spread", WidgetType.BOX, f"{m.name} by {c.name}", dims=[c.name], measures=ids,
                       encoding=Encoding(x=c.name, y=m.name), data_fit=0.85, insight=0.5,
                       facts=[f"{c.distinct} groups, {ctx.rows // c.distinct} rows each on average"])  # fmt: skip


def r_two_measures(ctx: Context) -> Iterator[Candidate]:
    """Two measures → scatter (sampled to 5k points when the data is larger than 10k)."""
    strong = {(c.a, c.b): c.r for c in ctx.profile.correlations}
    cols = [c.name for c in ctx.measure_columns[:4]]
    for a, b in itertools.combinations(cols, 2):
        r = strong.get((a, b), strong.get((b, a), 0.0))
        limit = 5000 if ctx.rows > MAX_SCATTER_POINTS else None
        yield make(ctx, "scatter", WidgetType.SCATTER, f"{b} vs {a}", dims=[a, b], limit=limit,
                   data_fit=0.8, insight=min(1.0, 0.1 + abs(r)), facts=[f"correlation {r:.2f}"])  # fmt: skip


def r_two_categories(ctx: Context) -> Iterator[Candidate]:
    """Two categories + measure → heatmap; split bar only for small x small."""
    groups = [c for c in ctx.group_columns if c.distinct <= 200][:4]
    for a, b in itertools.combinations(groups, 2):
        if [a.name, b.name] in ctx.profile.hierarchies or [
            b.name,
            a.name,
        ] in ctx.profile.hierarchies:
            continue
        mid = next((m for m in ctx.headline_measures(4) if ctx.additive(m)), "rows")
        top = MAX_HEATMAP_SIDE if a.distinct > MAX_HEATMAP_SIDE else None
        if b.distinct <= MAX_HEATMAP_SIDE:
            yield make(ctx, "two_cat", WidgetType.HEATMAP, f"{_label(ctx, mid)} by {a.name} and {b.name}",
                       dims=[a.name, b.name], measures=[mid], top_n=top, data_fit=0.75, insight=0.4,
                       facts=[f"{a.distinct} x {b.distinct} cells"])  # fmt: skip
        if a.distinct <= SMALL and b.distinct <= SMALL:
            yield make(ctx, "two_cat", WidgetType.BAR, f"{_label(ctx, mid)} by {a.name}, split by {b.name}",
                       dims=[a.name, b.name], measures=[mid], data_fit=0.75, insight=0.4)  # fmt: skip


def r_events(ctx: Context) -> Iterator[Candidate]:
    """Events with start and end → detail table (Gantt is post-MVP)."""
    times = {c.name for c in ctx.by_role({ColumnRole.TIME})}
    start = next((t for t in times if "start" in t), None)
    end = next((t for t in times if "end" in t or "finish" in t), None)
    if start and end:
        yield make(
            ctx,
            "events",
            WidgetType.TABLE,
            "Events",
            dims=[start, end],
            limit=200,
            data_fit=0.7,
            insight=0.3,
        )


def r_flows(ctx: Context) -> Iterator[Candidate]:
    """Source → target → heatmap of pairs (Sankey is post-MVP)."""
    names = {c.name for c in ctx.group_columns}
    for s, t in (("source", "target"), ("from", "to"), ("src", "dst")):
        if s in names and t in names:
            yield make(ctx, "flows", WidgetType.HEATMAP, f"{s} → {t}", dims=[s, t], measures=["rows"],
                       data_fit=0.75, insight=0.4)  # fmt: skip


def r_hierarchy(ctx: Context) -> Iterator[Candidate]:
    """Hierarchy + measure → stacked bar (parent, child) when the child is small, else bar by parent."""
    for parent, child in ctx.profile.hierarchies:
        if not ctx.has(parent, child) or parent not in {c.name for c in ctx.group_columns}:
            continue
        mid = next((m for m in ctx.headline_measures(4) if ctx.additive(m)), "rows")
        if ctx.distinct(child) <= MAX_SERIES and ctx.distinct(parent) <= MAX_CATEGORIES:
            yield make(ctx, "hierarchy", WidgetType.STACKED_BAR, f"{_label(ctx, mid)} by {parent} and {child}",
                       dims=[parent, child], measures=[mid], options=WidgetOptions(stack="stacked"),
                       data_fit=0.8, insight=0.45, facts=[f"{parent} → {child} hierarchy"])  # fmt: skip


def r_table(ctx: Context) -> Iterator[Candidate]:
    """Row-level detail → sortable table (always available)."""
    dims = [c.name for c in ctx.group_columns[:2]]
    measures = ctx.headline_measures(3) if dims else []
    if not dims:
        dims = [c.name for c in ctx.profile.columns[:6]]
    sort = [SortKey(field=measures[0], dir="desc")] if measures else []
    yield make(ctx, "table", WidgetType.TABLE, "Detail table", dims=dims, measures=measures, sort=sort,
               limit=500, data_fit=0.7, insight=0.2, facts=["always available"])  # fmt: skip


def r_signature(ctx: Context) -> Iterator[Candidate]:
    """The domain pack's signature visuals, where the data has the fields they need."""
    for v in ctx.pack.signature_visuals:
        if not ctx.has(*v.requires) or not all(m in ctx.pack_alias for m in v.measures):
            continue
        if (
            v.chart_type in (WidgetType.HISTOGRAM, WidgetType.BOX)
            and ctx.rows < MIN_DISTRIBUTION_ROWS
        ):
            continue
        if (
            v.chart_type == WidgetType.BOX
            and v.dimensions
            and ctx.rows / max(1, ctx.distinct(v.dimensions[0])) < MIN_ROWS_PER_CATEGORY
        ):
            continue
        dims, measures = list(v.dimensions), [ctx.pack_alias[m] for m in v.measures]
        grain: TimeGrain | None = None
        if v.time_grain == "build":
            grain = TimeGrain.BUILD
        elif v.time_grain == "auto" and dims:
            col = ctx.cols[dims[0]]
            grain = ctx.auto_grain(col)[0] if col.time else None
        encoding, options = None, None
        if v.chart_type == WidgetType.HISTOGRAM and v.distribution_of:
            dims, measures, options = [v.distribution_of], ["rows"], WidgetOptions(bins=30)
        elif v.chart_type == WidgetType.BOX and v.distribution_of:
            measures = ctx.distribution_measures(v.distribution_of)
            encoding = Encoding(x=dims[0] if dims else None, y=v.distribution_of)
        top = v.top_n
        if (
            v.chart_type in (WidgetType.BAR, WidgetType.PARETO)
            and dims
            and ctx.distinct(dims[0]) > MAX_CATEGORIES
        ):
            top = min(top or MAX_CATEGORIES, MAX_CATEGORIES)
        if v.chart_type == WidgetType.HEATMAP and dims and ctx.distinct(dims[0]) > MAX_HEATMAP_SIDE:
            top = min(top or MAX_HEATMAP_SIDE, MAX_HEATMAP_SIDE)
        if v.chart_type == WidgetType.DONUT and dims and ctx.distinct(dims[0]) > MAX_DONUT_PARTS:
            top = MAX_DONUT_PARTS - 1
        pts = ctx.points_for(dims[0], grain) if dims else 0
        if (
            v.chart_type == WidgetType.LINE
            and dims
            and ctx.is_time_dim(dims[0], grain)
            and pts < MIN_TIME_POINTS
        ):
            continue
        insight = (
            _trend(ctx, measures[0])[0] if measures and v.chart_type == WidgetType.LINE else 0.6
        )
        # A line over a non-time axis is a weaker fit than the canonical time + measure pattern.
        fit = (
            0.85
            if v.chart_type == WidgetType.LINE and dims and not ctx.is_time_dim(dims[0], grain)
            else 0.95
        )
        yield make(
            ctx, "signature", v.chart_type, v.title, dims=dims, measures=measures, grain=grain, top_n=top,
            sort=[SortKey(field=ctx.pack_alias.get(k.field, k.field), dir=k.dir) for k in v.sort], filters=list(v.filters), encoding=encoding, options=options,
            data_fit=fit, insight=max(0.55, insight), prior=1.0, facts=[f"{ctx.pack.label} signature visual"],
            option_id=ident("sig", v.id),
        )  # fmt: skip


RULES: tuple[Rule, ...] = (
    r_signature,
    r_kpi,
    r_time_measure,
    r_time_category,
    r_category,
    r_part_of_whole,
    r_distribution,
    r_spread,
    r_two_measures,
    r_two_categories,
    r_events,
    r_flows,
    r_hierarchy,
    r_table,
)
