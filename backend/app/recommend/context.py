"""Shared facts for candidate generation: profile lookups, the measure catalog, time axes
and the R3 guard rules (``violations``)."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

from app.domain_packs.loader import DomainPack, load_pack
from app.models.chart import VisualOption
from app.models.dataset import ColumnProfile, ColumnRole, DatasetProfile
from app.models.grammar import fields_used
from app.models.query import Measure, TimeGrain
from app.models.spec import WidgetType
from app.profiling.roles import GROUP_ROLES, MEASURE_ROLES
from app.query.compiler import GRAIN_SECONDS, top_n_dimension

MAX_SERIES = 7
MAX_CATEGORIES = 20
MAX_DONUT_PARTS = 6
MAX_BAR_PERIODS = 30
MIN_TIME_POINTS = 3
MIN_DISTRIBUTION_ROWS = 30
MIN_ROWS_PER_CATEGORY = 5
MAX_SCATTER_POINTS = 10_000
SMALL = 7  # "small x small" for split bars
MAX_HEATMAP_SIDE = 30
AUTO_GRAINS = (TimeGrain.MINUTE, TimeGrain.HOUR, TimeGrain.DAY, TimeGrain.WEEK)
MAX_AUTO_BUCKETS = 200


def ident(*parts: str) -> str:
    """A valid, stable Identifier from free text (≤ 64 chars)."""
    raw = "_".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_").lower() for p in parts if p)
    raw = raw if raw and (raw[0].isalpha() or raw[0] == "_") else f"m_{raw}"
    if len(raw) > 64:
        raw = raw[:55] + "_" + hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:8]
    return raw


@dataclass(frozen=True, slots=True)
class TimeAxis:
    field: str
    grain: TimeGrain | None  # None for a raw ordinal axis (e.g. threads)
    points: int


@dataclass
class Context:
    profile: DatasetProfile
    pack: DomainPack
    dataset: str = "ds"
    targets: dict[str, float] = field(default_factory=dict)
    catalog: dict[str, Measure] = field(default_factory=dict)
    pack_ids: set[str] = field(default_factory=set)  # catalog ids of domain-pack measures
    pack_alias: dict[str, str] = field(default_factory=dict)  # pack measure id -> catalog id

    def __post_init__(self) -> None:
        self.cols = {c.name: c for c in self.profile.columns}
        self._build_catalog()

    # -- lookups -----------------------------------------------------------
    @property
    def rows(self) -> int:
        return self.profile.row_count

    def distinct(self, name: str) -> int:
        return self.cols[name].distinct if name in self.cols else 0

    def has(self, *names: str) -> bool:
        return all(n in self.cols for n in names)

    def by_role(self, roles: frozenset[ColumnRole] | set[ColumnRole]) -> list[ColumnProfile]:
        return [c for c in self.profile.columns if c.role in roles]

    @property
    def measure_columns(self) -> list[ColumnProfile]:
        return [c for c in self.by_role(MEASURE_ROLES) if c.distinct > 1]

    @property
    def group_columns(self) -> list[ColumnProfile]:
        """Categorical columns worth grouping by (more than one value, not near-unique)."""
        skip = {"orig_tz", "run_id"}
        return [
            c
            for c in self.by_role(GROUP_ROLES)
            if 1 < c.distinct < max(2, self.rows) and c.name not in skip and c.distinct <= 500
        ]

    # -- measures ----------------------------------------------------------
    def _build_catalog(self) -> None:
        for m in self.pack.measures:
            if all(self.has(f) for f in m.requires) and all(
                self.has(f) for f in fields_used(m.ast())
            ):
                cid = self.add(Measure(id=m.id, expr=m.expr, format=m.format, label=m.label))
                self.pack_ids.add(cid)
                self.pack_alias[m.id] = cid
        self.add(Measure(id="rows", expr="count()", format="number", label="Rows"))
        for c in self.measure_columns:
            if c.role == ColumnRole.COUNT:
                self.add(
                    Measure(
                        id=ident("total", c.name), expr=f"sum({c.name})", label=f"Total {c.name}"
                    )
                )
            self.add(
                Measure(id=ident("avg", c.name), expr=f"avg({c.name})", label=f"Average {c.name}")
            )
            if c.role == ColumnRole.DURATION:
                self.add(
                    Measure(
                        id=ident("p95", c.name),
                        expr=f"percentile({c.name}, 95)",
                        label=f"p95 {c.name}",
                    )
                )

    def add(self, m: Measure) -> str:
        if m.id in self.cols:  # measure ids must not clash with field names
            m = m.model_copy(update={"id": ident(m.id, "m")})
        self.catalog.setdefault(m.id, m)
        return m.id

    def headline_measures(self, limit: int = 6) -> list[str]:
        """Measures worth trending: pack KPIs first, then per-column averages/totals."""
        kpis = [self.pack_alias[k.id] for k in self.pack.kpis if k.id in self.pack_alias]
        generic = [
            mid
            for mid in self.catalog
            if mid not in self.pack_ids and mid != "rows" and not mid.startswith(("p95_",))
        ]
        return (kpis + generic)[:limit] or ["rows"]

    def additive(self, measure_id: str) -> bool:
        expr = self.catalog[measure_id].expr.replace(" ", "")
        return expr.startswith(("count(", "count_if(", "sum("))

    def distribution_measures(self, field_name: str) -> list[str]:
        ids = []
        for suffix, expr in (
            ("min", f"min({field_name})"),
            ("p25", f"percentile({field_name}, 25)"),
            ("p50", f"percentile({field_name}, 50)"),
            ("p75", f"percentile({field_name}, 75)"),
            ("max", f"max({field_name})"),
        ):
            ids.append(self.add(Measure(id=ident(field_name, suffix), expr=expr)))
        return ids

    # -- time --------------------------------------------------------------
    def time_axes(self) -> list[TimeAxis]:
        axes: list[TimeAxis] = []
        if self.has("build") and self.distinct("build") >= 2:
            axes.append(TimeAxis("build", TimeGrain.BUILD, self.distinct("build")))
        times = sorted(
            (c for c in self.by_role({ColumnRole.TIME}) if c.time is not None),
            key=lambda c: -c.distinct,
        )
        if times:
            c = times[0]
            grain, points = self.auto_grain(c)
            if grain is not None:
                axes.append(TimeAxis(c.name, grain, points))
        return axes

    def auto_grain(self, c: ColumnProfile) -> tuple[TimeGrain | None, int]:
        """Finest grain that gives at most MAX_AUTO_BUCKETS buckets and isn't finer than the data."""
        assert c.time is not None
        span = c.time.span_seconds
        gap = c.time.median_gap_seconds or 0
        for g in AUTO_GRAINS:
            secs = GRAIN_SECONDS[g]
            if secs < gap * 0.99:
                continue
            points = min(c.distinct, math.floor(span / secs) + 1)
            if points <= MAX_AUTO_BUCKETS:
                return g, points
        return TimeGrain.WEEK, min(c.distinct, int(span // GRAIN_SECONDS[TimeGrain.WEEK]) + 1)

    def points_for(self, dim: str, grain: TimeGrain | None) -> int:
        c = self.cols.get(dim)
        if c is None:
            return 0
        if grain in GRAIN_SECONDS and c.time is not None:
            return min(c.distinct, int(c.time.span_seconds // GRAIN_SECONDS[grain]) + 1)
        return c.distinct

    def is_time_dim(self, dim: str, grain: TimeGrain | None) -> bool:
        c = self.cols.get(dim)
        return c is not None and (
            c.role == ColumnRole.TIME or (dim == "build" and grain == TimeGrain.BUILD)
        )


def violations(option: VisualOption, ctx: Context) -> list[str]:
    """R3 guard rules. An empty list means the option is allowed."""
    q = option.query
    t = option.chart_type
    dims = q.dimensions
    out: list[str] = []
    top_dim = _top_dim(option, ctx)

    def within(dim: str, cap: int, *, other_counts: bool = False) -> bool:
        """``dim`` fits ``cap`` groups, or top N (+ "Other") is applied to it within the cap."""
        if ctx.distinct(dim) <= cap:
            return True
        if q.top_n is None or top_dim != dim:
            return False
        return q.top_n + (1 if other_counts else 0) <= cap

    if t == WidgetType.KPI and dims:
        out.append("a KPI has no dimension")
    if t in (WidgetType.LINE, WidgetType.AREA, WidgetType.BAR, WidgetType.STACKED_BAR) and dims:
        x = dims[0]
        if ctx.is_time_dim(x, q.time_grain):
            pts = ctx.points_for(x, q.time_grain)
            if pts < MIN_TIME_POINTS and t in (WidgetType.LINE, WidgetType.AREA):
                out.append("fewer than 3 time points")
            if t == WidgetType.BAR and pts > MAX_BAR_PERIODS:
                out.append("columns over more than 30 periods")
        if len(dims) > 1 and not within(dims[1], MAX_SERIES):
            out.append("more than 7 series without top N")
        if (
            t == WidgetType.BAR
            and len(dims) > 1
            and not (ctx.distinct(dims[0]) <= SMALL and within(dims[1], SMALL))
        ):
            out.append("split bars only for small x small")
    categorical = (
        t in (WidgetType.BAR, WidgetType.PARETO)
        and dims
        and not ctx.is_time_dim(dims[0], q.time_grain)
    )
    if categorical and not within(dims[0], MAX_CATEGORIES):
        out.append("more than 20 categories without top N")
    if t == WidgetType.DONUT and (
        not dims or not within(dims[0], MAX_DONUT_PARTS, other_counts=True)
    ):
        out.append("a donut needs at most 6 parts")
    if t in (WidgetType.HISTOGRAM, WidgetType.BOX) and ctx.rows < MIN_DISTRIBUTION_ROWS:
        out.append("fewer than 30 rows for a distribution")
    if (
        t == WidgetType.BOX
        and dims
        and ctx.rows / max(1, ctx.distinct(dims[0])) < MIN_ROWS_PER_CATEGORY
    ):
        out.append("fewer than 5 rows per category")
    if t == WidgetType.SCATTER and min(ctx.rows, q.limit or ctx.rows) > MAX_SCATTER_POINTS:
        out.append("more than 10k points in a scatter")
    if (
        t == WidgetType.HEATMAP
        and len(dims) == 2
        and not all(within(d, MAX_HEATMAP_SIDE) for d in dims)
    ):
        out.append("heatmap side larger than 30")
    return out


def _top_dim(option: VisualOption, ctx: Context) -> str | None:
    q = option.query
    if q.top_n is None or not q.dimensions:
        return None
    return top_n_dimension(
        q.dimensions, {c.name: c.type for c in ctx.profile.columns}, q.time_grain
    )


def context_for(
    profile: DatasetProfile, dataset: str = "ds", targets: dict[str, float] | None = None
) -> Context:
    return Context(
        profile=profile,
        pack=load_pack(profile.domain or "generic"),
        dataset=dataset,
        targets=targets or {},
    )
