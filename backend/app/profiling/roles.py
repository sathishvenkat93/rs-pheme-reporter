"""Column role inference. The rules are data: the first matching row wins."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.dataset import ColumnRole, ColumnType

NUMERIC = frozenset({ColumnType.INTEGER, ColumnType.FLOAT})
TEXTUAL = frozenset({ColumnType.STRING})


@dataclass(frozen=True, slots=True)
class ColumnFacts:
    name: str
    type: ColumnType
    rows: int
    non_null: int
    distinct: int
    avg_len: float | None = None
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True, slots=True)
class RoleRule:
    role: ColumnRole
    why: str
    types: frozenset[ColumnType] | None = None
    name: str | None = None  # regex on the lowercased column name
    max_distinct: int | None = None
    min_unique_ratio: float | None = None  # distinct / non-null
    min_rows: int = 0
    min_avg_len: float | None = None
    unit_interval: bool = False  # all values within [0, 1]

    def matches(self, f: ColumnFacts) -> bool:
        if self.types is not None and f.type not in self.types:
            return False
        if self.name is not None and not re.search(self.name, f.name.lower()):
            return False
        if self.max_distinct is not None and f.distinct > self.max_distinct:
            return False
        if self.min_unique_ratio is not None and (
            f.non_null == 0 or f.distinct / f.non_null < self.min_unique_ratio
        ):
            return False
        if f.non_null < self.min_rows:
            return False
        if self.min_avg_len is not None and (f.avg_len is None or f.avg_len < self.min_avg_len):
            return False
        return not (
            self.unit_interval
            and not (f.min is not None and f.max is not None and f.min >= 0 and f.max <= 1)
        )


ROLE_RULES: tuple[RoleRule, ...] = (
    RoleRule(
        ColumnRole.TIME,
        "date or timestamp type",
        types=frozenset({ColumnType.TIMESTAMP, ColumnType.DATE}),
    ),
    RoleRule(
        ColumnRole.STATUS,
        "status-like name with few values",
        name=r"(^|_)(status|result|outcome|state|verdict|level|severity|success|passed|ok)$",
        max_distinct=6,
    ),
    RoleRule(ColumnRole.STATUS, "true/false values", types=frozenset({ColumnType.BOOLEAN})),
    RoleRule(
        ColumnRole.IDENTIFIER,
        "id-like name",
        name=r"(^|_)(id|uuid|guid|key|sku|hash)$|^(run_id|trace_id|request_id|span_id)$",
    ),
    RoleRule(
        ColumnRole.DURATION,
        "numeric with a time unit or duration name",
        types=NUMERIC,
        name=r"(_ms|_s|_sec|_secs|_seconds|_millis|_us)$|duration|elapsed|latency|response_time|^time$",
    ),
    RoleRule(
        ColumnRole.RATIO,
        "numeric with a rate/ratio name",
        types=NUMERIC,
        name=r"rate|ratio|pct|percent|share",
    ),
    RoleRule(
        ColumnRole.COUNT,
        "whole numbers with a count name",
        types=frozenset({ColumnType.INTEGER}),
        name=r"(^|_)(count|num|n|total|qty|quantity|orders|requests|hits|threads|users|errors|failures|visits)(_|$)",
    ),
    RoleRule(
        ColumnRole.GEO,
        "place-like name",
        name=r"(^|_)(country|city|latitude|longitude|lat|lon|lng|geo|postcode|zip)(_|$)",
    ),
    RoleRule(
        ColumnRole.RATIO,
        "fractions between 0 and 1",
        types=frozenset({ColumnType.FLOAT}),
        unit_interval=True,
    ),
    RoleRule(ColumnRole.MEASURE, "numeric", types=NUMERIC),
    RoleRule(
        ColumnRole.IDENTIFIER,
        "mostly unique text",
        types=TEXTUAL,
        min_unique_ratio=0.9,
        min_rows=20,
    ),
    RoleRule(ColumnRole.TEXT, "long free text", types=TEXTUAL, min_avg_len=40),
    RoleRule(ColumnRole.CATEGORY, "text with repeated values"),
)

MEASURE_ROLES = frozenset(
    {ColumnRole.MEASURE, ColumnRole.DURATION, ColumnRole.COUNT, ColumnRole.RATIO}
)
GROUP_ROLES = frozenset(
    {ColumnRole.CATEGORY, ColumnRole.STATUS, ColumnRole.GEO, ColumnRole.HIERARCHY_LEVEL}
)


def infer_role(facts: ColumnFacts) -> ColumnRole:
    return next(r.role for r in ROLE_RULES if r.matches(facts))
