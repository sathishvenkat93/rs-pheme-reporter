"""Feasibility verdicts (M7 produces them)."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, model_validator

from app.models.chart import ChartRequest
from app.models.common import StrictModel


class VerdictKind(StrEnum):
    FEASIBLE = "feasible"
    ADJUSTED = "adjusted"
    NOT_FEASIBLE = "not_feasible"
    NEEDS_CLARIFICATION = "needs_clarification"


class AppliedChange(StrictModel):
    """The exact change an adjusted verdict made, so the UI can show before and after."""

    description: str = Field(min_length=1)
    before: dict[str, JsonValue]
    after: dict[str, JsonValue]


class Verdict(StrictModel):
    kind: VerdictKind
    request_id: str
    check: str | None = None
    reasons: list[str] = Field(default_factory=list)
    alternatives: list[ChartRequest] = Field(default_factory=list)
    applied_change: AppliedChange | None = None
    candidates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind == VerdictKind.ADJUSTED and self.applied_change is None:
            raise ValueError("an adjusted verdict must carry applied_change")
        if self.kind != VerdictKind.ADJUSTED and self.applied_change is not None:
            raise ValueError("only adjusted verdicts carry applied_change")
        if (
            self.kind in (VerdictKind.NOT_FEASIBLE, VerdictKind.NEEDS_CLARIFICATION)
            and not self.reasons
        ):
            raise ValueError(f"a {self.kind.value} verdict needs at least one reason")
        if self.kind == VerdictKind.NEEDS_CLARIFICATION and not self.candidates:
            raise ValueError("needs_clarification must offer candidates")
        return self
