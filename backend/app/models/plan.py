"""Draft plans, the operations that change them, and the decisions that gate approval."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.models.chart import ChartRequest, VisualOption
from app.models.common import FieldName, Identifier, StrictModel
from app.models.query import Filter, Measure
from app.models.spec import Annotation, Encoding, GlobalFilter, Threshold, WidgetType


class AddOp(StrictModel):
    op: Literal["add"] = "add"
    option: VisualOption


class RemoveOp(StrictModel):
    op: Literal["remove"] = "remove"
    option_id: Identifier


class ChangeTypeOp(StrictModel):
    op: Literal["change_type"] = "change_type"
    option_id: Identifier
    chart_type: WidgetType


class ChangeEncodingOp(StrictModel):
    op: Literal["change_encoding"] = "change_encoding"
    option_id: Identifier
    dimensions: list[FieldName] | None = None
    measures: list[Measure] | None = None
    encoding: Encoding | None = None

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.dimensions is None and self.measures is None and self.encoding is None:
            raise ValueError("change_encoding must change dimensions, measures or encoding")
        return self


class FilterOp(StrictModel):
    """Adds a filter to one option, or a global filter when ``option_id`` is None."""

    op: Literal["filter"] = "filter"
    option_id: Identifier | None = None
    filter: Filter


class DeriveMetricOp(StrictModel):
    op: Literal["derive_metric"] = "derive_metric"
    measure: Measure


class LayoutOp(StrictModel):
    op: Literal["layout"] = "layout"
    hints: list[str] = Field(min_length=1)


class StyleOp(StrictModel):
    op: Literal["style"] = "style"
    option_id: Identifier
    thresholds: list[Threshold] = Field(min_length=1)


class AnnotateOp(StrictModel):
    op: Literal["annotate"] = "annotate"
    option_id: Identifier
    annotation: Annotation


PlanOp = Annotated[
    AddOp
    | RemoveOp
    | ChangeTypeOp
    | ChangeEncodingOp
    | FilterOp
    | DeriveMetricOp
    | LayoutOp
    | StyleOp
    | AnnotateOp,
    Field(discriminator="op"),
]


class DecisionChoice(StrEnum):
    ACCEPT_ALTERNATIVE = "accept_alternative"
    KEEP_AS_ASKED = "keep_as_asked"
    DROP = "drop"
    PROVIDE_DATA = "provide_data"
    CHOOSE_CANDIDATE = "choose_candidate"


class Decision(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    item: str = Field(min_length=1, max_length=64)  # an option id or a request id
    reason: str = Field(min_length=1)
    options: list[DecisionChoice] = Field(min_length=1)
    choice: DecisionChoice | None = None
    choice_detail: str | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.choice is None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        decided = (self.choice, self.decided_by, self.decided_at)
        if any(v is None for v in decided) and any(v is not None for v in decided):
            raise ValueError("choice, decided_by and decided_at are set together")
        if self.choice is not None and self.choice not in self.options:
            raise ValueError(f"choice {self.choice.value!r} is not one of the offered options")
        return self


class DraftPlan(StrictModel):
    id: UUID
    session_id: UUID
    version: int = Field(ge=1)
    options: list[VisualOption] = Field(default_factory=list)
    requests: list[ChartRequest] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    filters: list[GlobalFilter] = Field(default_factory=list)
    layout_hints: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        ids = [o.id for o in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate option ids")
        known = set(ids) | {r.id for r in self.requests}
        for d in self.decisions:
            if d.item not in known:
                raise ValueError(
                    f"decision {d.id!r} refers to unknown option or request {d.item!r}"
                )
        if len({d.id for d in self.decisions}) != len(self.decisions):
            raise ValueError("duplicate decision ids")
        return self

    @property
    def open_decisions(self) -> list[Decision]:
        return [d for d in self.decisions if d.is_open]
