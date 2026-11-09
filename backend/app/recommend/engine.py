"""recommend(): candidates from every rule, scored (R4), diversified (MMR) and ranked.

Requested charts are pinned above everything else, in request order. M6 resolves request
fields by exact name; M7 replaces the resolver with the full feasibility check.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.core.config import get_settings
from app.models.chart import ChartRequest, FieldRoleHint, OptionStatus, ScoreBreakdown, VisualOption
from app.models.dataset import DatasetProfile
from app.models.spec import WidgetType
from app.profiling.roles import MEASURE_ROLES
from app.recommend.context import Context, context_for, ident
from app.recommend.rules import RULES, Candidate, make
from app.recommend.scoring import REDUNDANCY_SCALE, base_score, similarity

RECOMMENDED = 10
# Turns a request into a candidate, or None when it can't be built (M7 explains why).
RequestResolver = Callable[[ChartRequest, Context], Candidate | None]


def exact_resolver(request: ChartRequest, ctx: Context) -> Candidate | None:
    """Build a request whose field phrases are exact column or measure names."""
    dims: list[str] = []
    measures: list[str] = []
    for phrase in request.field_phrases:
        name = phrase.text.strip()
        if name in ctx.catalog:
            measures.append(name)
        elif name in ctx.cols:
            col = ctx.cols[name]
            if (
                col.role in MEASURE_ROLES
                and phrase.role in (FieldRoleHint.Y, FieldRoleHint.MEASURE, None)
                and dims
            ):
                measures.append(
                    ctx.add(ctx.catalog[ident("avg", name)])
                    if ident("avg", name) in ctx.catalog
                    else "rows"
                )
            elif col.role in MEASURE_ROLES and phrase.role in (
                FieldRoleHint.Y,
                FieldRoleHint.MEASURE,
            ):
                measures.append(ident("avg", name) if ident("avg", name) in ctx.catalog else "rows")
            else:
                dims.append(name)
        else:
            return None
    chart = request.widget_type()
    if chart is None:
        if request.chart_type is not None:
            return None  # unsupported chart type: M7's renderer check explains it
        chart = (
            WidgetType.LINE
            if dims and ctx.is_time_dim(dims[0], None)
            else WidgetType.BAR
            if dims
            else WidgetType.KPI
        )
    if not measures and chart != WidgetType.TABLE:
        measures = ["rows"]
    grain = None
    if dims and ctx.cols[dims[0]].time is not None:
        grain = ctx.auto_grain(ctx.cols[dims[0]])[0]
    elif dims and dims[0] == "build":
        from app.models.query import TimeGrain

        grain = TimeGrain.BUILD
    title = request.raw_text[:200]
    return make(ctx, "requested", chart, title, dims=dims, measures=measures, grain=grain, top_n=request.top_n,
                data_fit=0.9, insight=0.5, facts=["requested"], option_id=ident("req", request.id))  # fmt: skip


def _finish(
    c: Candidate, terms: dict[str, float], redundancy: float, status: OptionStatus
) -> VisualOption:
    total = round(terms["base"] - redundancy, 4)
    score = ScoreBreakdown(
        data_fit=terms["data_fit"], intent=terms["intent"], insight=terms["insight"],
        perception=terms["perception"], domain_prior=terms["domain_prior"], redundancy=round(redundancy, 4),
        clutter=terms["clutter"], total=total, facts=c.facts,
    )  # fmt: skip
    return c.option.model_copy(
        update={"score": score, "status": status, "rationale": "; ".join(c.facts)}
    )


def candidates(ctx: Context) -> list[Candidate]:
    seen: dict[tuple[object, ...], Candidate] = {}
    for rule in RULES:
        for c in rule(ctx):
            q = c.option.query
            key = (
                c.option.chart_type,
                tuple(q.dimensions),
                tuple(sorted(q.measures)),
                q.time_grain,
                q.top_n,
            )
            if key not in seen or c.prior > seen[key].prior:
                seen[key] = c
    return list(seen.values())


def recommend(
    profiles: Sequence[DatasetProfile],
    requests: Sequence[ChartRequest] | None = None,
    k: int = 25,
    *,
    targets: dict[str, float] | None = None,
    resolver: RequestResolver = exact_resolver,
) -> list[VisualOption]:
    """Ranked candidates (requested first), each with its score breakdown and profile facts."""
    weights = get_settings().recommend_weights
    pinned: list[VisualOption] = []
    pool: list[tuple[Candidate, dict[str, float]]] = []
    for i, profile in enumerate(profiles):
        ctx = context_for(
            profile, dataset="ds" if len(profiles) == 1 else f"ds{i + 1}", targets=targets
        )
        for req in requests or []:
            c = resolver(req, ctx)
            if c is not None:
                opt = _finish(c, base_score(c, 1.0, weights), 0.0, OptionStatus.REQUESTED)
                pinned.append(opt.model_copy(update={"request_id": req.id}))
        for c in candidates(ctx):
            pool.append((c, base_score(c, 0.0, weights)))

    # Greedy MMR: repeatedly take the best base score minus similarity to what's chosen.
    chosen: list[tuple[Candidate, dict[str, float], float]] = []
    remaining = sorted(pool, key=lambda p: (-p[1]["base"], p[0].option.id))
    w_red = weights.get("redundancy", 1.0) * REDUNDANCY_SCALE
    while remaining and len(chosen) < max(0, k - len(pinned)):
        best_i, best_val, best_pen = 0, float("-inf"), 0.0
        for i, (c, terms) in enumerate(remaining):
            # Cumulative: each similar chart already chosen adds to the penalty (capped at 1).
            pen = w_red * min(1.0, sum(similarity(c, o) for o, _, _ in chosen))
            if terms["base"] - pen > best_val:
                best_i, best_val, best_pen = i, terms["base"] - pen, pen
        c, terms = remaining.pop(best_i)
        chosen.append((c, terms, best_pen))

    ranked = [
        _finish(
            c, terms, pen, OptionStatus.RECOMMENDED if i < RECOMMENDED else OptionStatus.OPTIONAL
        )
        for i, (c, terms, pen) in enumerate(chosen)
    ]
    return pinned + ranked
