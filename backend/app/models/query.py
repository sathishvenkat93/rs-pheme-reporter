"""Semantic queries and measures: the only path from a spec to rows."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from app.models.common import FieldName, Identifier, Scalar, StrictModel
from app.models.grammar import MeasureExpr, format_measure, parse_measure


class TimeGrain(StrEnum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    BUILD = "build"


class EqFilter(StrictModel):
    op: Literal["eq"] = "eq"
    field: FieldName
    value: Scalar


class InFilter(StrictModel):
    op: Literal["in"] = "in"
    field: FieldName
    values: list[Scalar] = Field(min_length=1)


class RangeFilter(StrictModel):
    op: Literal["range"] = "range"
    field: FieldName
    gte: Scalar | None = None
    lte: Scalar | None = None

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if self.gte is None and self.lte is None:
            raise ValueError("a range filter needs gte, lte or both")
        return self


class RelativeTimeFilter(StrictModel):
    op: Literal["relative_time"] = "relative_time"
    field: FieldName
    last: int = Field(gt=0)
    unit: Literal["minute", "hour", "day", "week"]
    anchor: Literal["data_max", "now"] = "data_max"


class LastNFilter(StrictModel):
    """Keep the last ``n`` distinct values of an ordinal field, e.g. the last 7 builds."""

    op: Literal["last_n"] = "last_n"
    field: FieldName
    n: int = Field(gt=0)


Filter = Annotated[
    EqFilter | InFilter | RangeFilter | RelativeTimeFilter | LastNFilter,
    Field(discriminator="op"),
]


class SortKey(StrictModel):
    field: FieldName
    dir: Literal["asc", "desc"] = "asc"


MeasureFormat = Literal["number", "percent", "duration_ms", "bytes", "rate"]


class Measure(StrictModel):
    """A named measure. ``expr`` must parse in the measure grammar; it is never SQL."""

    id: Identifier
    expr: str = Field(min_length=1, max_length=500)
    format: MeasureFormat | None = None
    label: str | None = None

    @field_validator("expr")
    @classmethod
    def _parses(cls, v: str) -> str:
        parse_measure(v)  # raises MeasureSyntaxError (a ValueError) with a readable message
        return v

    def ast(self) -> MeasureExpr:
        return parse_measure(self.expr)

    def canonical_expr(self) -> str:
        return format_measure(self.ast())


class SemanticQuery(StrictModel):
    """What to compute. ``measures`` are ids resolved against a measure catalog."""

    dataset: Identifier
    dimensions: list[FieldName] = Field(default_factory=list)
    measures: list[Identifier] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time_grain: TimeGrain | None = None
    sort: list[SortKey] = Field(default_factory=list)
    top_n: int | None = Field(default=None, gt=0)
    limit: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if not self.dimensions and not self.measures:
            raise ValueError("a query needs at least one dimension or measure")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("duplicate dimensions")
        if len(set(self.measures)) != len(self.measures):
            raise ValueError("duplicate measures")
        if self.top_n is not None and not self.dimensions:
            raise ValueError("top_n needs a dimension")
        return self
