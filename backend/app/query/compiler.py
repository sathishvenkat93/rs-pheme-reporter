"""SemanticQuery → DuckDB SQL over the ``data`` view.

Every identifier is checked against the dataset's columns (or the query's measure ids)
and double-quoted; every literal is a numbered parameter (``$1``, ``$2`` …). Measure
expressions arrive as ASTs from Pheme's grammar, so no user text is ever spliced in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.dataset import ColumnType
from app.models.grammar import (
    Agg,
    And,
    Compare,
    InList,
    MeasureExpr,
    Named,
    Not,
    Or,
    Pred,
    Rate,
    Ratio,
    fields_used,
)
from app.models.query import (
    EqFilter,
    Filter,
    InFilter,
    LastNFilter,
    Measure,
    RangeFilter,
    RelativeTimeFilter,
    SemanticQuery,
    TimeGrain,
)
from app.query.engine import quote

GRAIN_SECONDS = {
    TimeGrain.MINUTE: 60,
    TimeGrain.HOUR: 3600,
    TimeGrain.DAY: 86400,
    TimeGrain.WEEK: 604800,
}
_UNIT_FN = {"minute": "to_minutes", "hour": "to_hours", "day": "to_days", "week": "to_weeks"}
_CMP = {"==": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
_AGG = {"sum": "sum", "avg": "avg", "min": "min", "max": "max"}
TIME_TYPES = frozenset({ColumnType.TIMESTAMP, ColumnType.DATE})
FLAKY_KEY = ("build", "suite", "case")
OTHER = "Other"


class QueryError(ValueError):
    """A query Pheme can't run. The message is meant for users."""


@dataclass
class Compiled:
    sql: str
    params: list[Any] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


class _Builder:
    def __init__(
        self, columns: dict[str, ColumnType], time_field: str | None, grain: TimeGrain | None
    ) -> None:
        self.columns = columns
        self.time_field = time_field
        self.grain = grain
        self.params: list[Any] = []
        self.needs_flaky = False

    def param(self, value: Any) -> str:
        self.params.append(value)
        return f"${len(self.params)}"

    def col(self, name: str) -> str:
        if name not in self.columns:
            raise QueryError(f"The dataset has no field named {name!r}.")
        return quote(name)

    # predicates ------------------------------------------------------------
    def pred(self, p: Pred) -> str:
        match p:
            case Compare(f, op, value):
                return f"{self.col(f)} {_CMP[op]} {self.param(value)}"
            case InList(f, values):
                return f"{self.col(f)} IN ({', '.join(self.param(v) for v in values)})"
            case And(items):
                return "(" + " AND ".join(self.pred(i) for i in items) + ")"
            case Or(items):
                return "(" + " OR ".join(self.pred(i) for i in items) + ")"
            case Not(item):
                return f"(NOT {self.pred(item)})"
        raise AssertionError(p)  # pragma: no cover

    # measures --------------------------------------------------------------
    def measure(self, m: MeasureExpr) -> str:
        match m:
            case Agg("count"):
                return "count(*)"
            case Agg("count_if", pred=pred) if pred is not None:
                return f"count(*) FILTER (WHERE {self.pred(pred)})"
            case Agg("count_distinct", field=f) if f:
                return f"count(DISTINCT {self.col(f)})"
            case Agg("percentile", field=f, p=p) if f and p is not None:
                return f"quantile_cont({self.col(f)}, {self.param(p / 100)})"
            case Agg(func, field=f) if f and func in _AGG:
                return f"{_AGG[func]}({self.col(f)})"
            case Ratio(n, d):
                return f"(CAST({self.measure(n)} AS DOUBLE) / NULLIF({self.measure(d)}, 0))"
            case Rate(inner, per):
                scale = 60 if per == "minute" else 1
                if self.grain in GRAIN_SECONDS:
                    seconds = str(GRAIN_SECONDS[self.grain])
                else:
                    if self.time_field is None:
                        raise QueryError("A rate needs a time field in the dataset.")
                    t = self.col(self.time_field)
                    seconds = f"NULLIF(epoch(max({t})) - epoch(min({t})), 0)"
                return f"(CAST({self.measure(inner)} AS DOUBLE) * {scale} / {seconds})"
            case Named("apdex", field=f, threshold=t) if f and t:
                c, lo, hi = self.col(f), self.param(t), self.param(4 * t)
                return (
                    f"((count(*) FILTER (WHERE {c} <= {lo}) + 0.5 * count(*) FILTER "
                    f"(WHERE {c} > {lo} AND {c} <= {hi})) / NULLIF(count(*), 0))"
                )
            case Named("flaky_rate"):
                for k in (*FLAKY_KEY, "status"):
                    self.col(k)
                self.needs_flaky = True
                key = "concat_ws(chr(31), " + ", ".join(quote(k) for k in FLAKY_KEY) + ")"
                return (
                    f"(CAST(count(DISTINCT {key}) FILTER (WHERE __flaky) AS DOUBLE) / "
                    f"NULLIF(count(DISTINCT {key}), 0))"
                )
        raise QueryError("This measure can't be computed.")  # pragma: no cover

    # filters ---------------------------------------------------------------
    def filter(self, f: Filter) -> str:
        c = self.col(f.field)
        match f:
            case EqFilter():
                return f"{c} = {self.param(f.value)}"
            case InFilter():
                return f"{c} IN ({', '.join(self.param(v) for v in f.values)})"
            case RangeFilter():
                parts = []
                if f.gte is not None:
                    parts.append(f"{c} >= {self.param(f.gte)}")
                if f.lte is not None:
                    parts.append(f"{c} <= {self.param(f.lte)}")
                return " AND ".join(parts)
            case RelativeTimeFilter():
                if self.columns[f.field] not in TIME_TYPES:
                    raise QueryError(f"{f.field!r} isn't a date or time field.")
                anchor = f"(SELECT max({c}) FROM data)" if f.anchor == "data_max" else "now()"
                return f"{c} >= {anchor} - {_UNIT_FN[f.unit]}(CAST({self.param(f.last)} AS BIGINT))"
            case LastNFilter():
                return (
                    f"{c} IN (SELECT v FROM (SELECT DISTINCT {c} AS v FROM data WHERE {c} IS NOT NULL) "
                    f"ORDER BY {self.ordinal('v', f.field, desc=True)} LIMIT {self.param(f.n)})"
                )
        raise AssertionError(f)  # pragma: no cover

    def ordinal(self, expr: str, name: str, desc: bool = False) -> str:
        """Order build-like text numerically when it is numeric, else as text."""
        d = " DESC" if desc else ""
        if self.columns.get(name) == ColumnType.STRING:
            return f"TRY_CAST({expr} AS DOUBLE){d} NULLS LAST, {expr}{d}"
        return f"{expr}{d}"

    def dim(self, name: str) -> str:
        c = self.col(name)
        if self.grain in GRAIN_SECONDS and self.columns[name] in TIME_TYPES:
            return f"date_trunc('{self.grain.value}', {c})"
        return c


def compile_query(
    query: SemanticQuery,
    columns: dict[str, ColumnType],
    measures: dict[str, Measure],
    *,
    time_field: str | None = None,
    row_cap: int = 10_000,
) -> Compiled:
    """Compile ``query``; ``measures`` resolves the query's measure ids."""
    b = _Builder(columns, time_field, query.time_grain)
    for name in query.measures:
        if name not in measures:
            raise QueryError(f"The measure {name!r} isn't defined.")
        if name in query.dimensions or name in columns:
            raise QueryError(f"The measure id {name!r} clashes with a field name.")
        for f in fields_used(measures[name].ast()):
            b.col(f)
    if query.time_grain is TimeGrain.BUILD and "build" not in query.dimensions:
        raise QueryError("The build grain needs 'build' as a dimension.")

    where = " AND ".join(f"({b.filter(f)})" for f in query.filters) or "TRUE"
    measure_sql = {m: b.measure(measures[m].ast()) for m in query.measures}
    flaky = ""
    if b.needs_flaky:
        w = "PARTITION BY " + ", ".join(quote(k) for k in FLAKY_KEY)
        flaky = (
            f", (bool_or({quote('status')} = 'passed') OVER ({w}) AND "
            f"bool_or({quote('status')} IN ('failed', 'error')) OVER ({w})) AS __flaky"
        )
    ctes = [f"base AS (SELECT *{flaky} FROM data WHERE {where})"]
    source = "base"

    dims = list(query.dimensions)
    dim_sql = {d: b.dim(d) for d in dims}
    if query.top_n is not None:
        target = next((d for d in dims if columns[d] not in TIME_TYPES), dims[0])
        rank = measure_sql[query.measures[0]] if query.measures else "count(*)"
        tv = f"CAST({dim_sql[target]} AS VARCHAR)"
        ctes.append(
            f"top AS (SELECT {tv} AS __v FROM base GROUP BY 1 ORDER BY {rank} DESC NULLS LAST, 1 "
            f"LIMIT {b.param(query.top_n)})"
        )
        ctes.append(
            f"shaped AS (SELECT *, CASE WHEN {tv} IN (SELECT __v FROM top) THEN {tv} "
            f"ELSE {b.param(OTHER)} END AS __top FROM base)"
        )
        source = "shaped"
        dim_sql[target] = "__top"

    select = [f"{dim_sql[d]} AS {quote(d)}" for d in dims]
    select += [f"{measure_sql[m]} AS {quote(m)}" for m in query.measures]
    sql = f"WITH {', '.join(ctes)} SELECT {', '.join(select)} FROM {source}"
    if query.measures and dims:
        sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))

    order: list[str] = []
    outputs = set(dims) | set(query.measures)
    for key in query.sort:
        if key.field not in outputs:
            raise QueryError(f"Can't sort by {key.field!r}: it isn't in the query.")
        if key.field in dims:
            order.append(b.ordinal(quote(key.field), key.field, key.dir == "desc"))
        else:
            order.append(f"{quote(key.field)} {key.dir.upper()} NULLS LAST")
    if not order:
        order = [b.ordinal(quote(d), d) for d in dims]
    if order:  # outer query: names refer to output columns (e.g. the "Other"-folded dimension)
        sql = f"SELECT * FROM ({sql}) AS q ORDER BY " + ", ".join(order)
    limit = min(query.limit or row_cap, row_cap)
    sql += f" LIMIT {b.param(limit + 1)}"  # one extra row tells the executor it truncated
    return Compiled(sql=sql, params=b.params, columns=[*dims, *query.measures])
