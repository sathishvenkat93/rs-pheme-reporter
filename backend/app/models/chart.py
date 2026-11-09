"""Chart requests (what the user asked for) and visual options (what Pheme proposes)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.models.common import FieldName, Identifier, StrictModel
from app.models.query import Measure, SemanticQuery
from app.models.spec import Encoding, WidgetOptions, WidgetType

# User words for chart types that map onto MVP widget types.
CHART_TYPE_SYNONYMS: dict[str, WidgetType] = {
    "pie": WidgetType.DONUT,
    "pie chart": WidgetType.DONUT,
    "doughnut": WidgetType.DONUT,
    "column": WidgetType.BAR,
    "columns": WidgetType.BAR,
    "bar chart": WidgetType.BAR,
    "lollipop": WidgetType.BAR,
    "grouped bar": WidgetType.BAR,
    "stacked bar": WidgetType.STACKED_BAR,
    "stacked column": WidgetType.STACKED_BAR,
    "line chart": WidgetType.LINE,
    "trend": WidgetType.LINE,
    "time series": WidgetType.LINE,
    "sparkline": WidgetType.KPI,
    "gauge": WidgetType.KPI,
    "bullet": WidgetType.KPI,
    "stat": WidgetType.KPI,
    "number": WidgetType.KPI,
    "box plot": WidgetType.BOX,
    "boxplot": WidgetType.BOX,
    "distribution": WidgetType.HISTOGRAM,
    "scatter plot": WidgetType.SCATTER,
    "grid": WidgetType.TABLE,
}


class FieldRoleHint(StrEnum):
    X = "x"
    Y = "y"
    SPLIT = "split"
    MEASURE = "measure"
    SIZE = "size"


class FieldPhrase(StrictModel):
    """The user's own words for a field, before the M7 resolver maps them to a column."""

    text: str = Field(min_length=1, max_length=200)
    role: FieldRoleHint | None = None


class ChartRequest(StrictModel):
    """An unresolved request. ``chart_type`` is free text so unsupported types can be expressed."""

    id: str = Field(min_length=1, max_length=64)
    raw_text: str = Field(min_length=1, max_length=2000)
    chart_type: str | None = Field(default=None, max_length=100)
    field_phrases: list[FieldPhrase] = Field(default_factory=list)
    aggregation: str | None = Field(default=None, max_length=100)
    filter_phrases: list[str] = Field(default_factory=list)
    time_grain: str | None = Field(default=None, max_length=50)
    top_n: int | None = Field(default=None, gt=0)
    source: Literal["user", "agent"] = "user"

    def widget_type(self) -> WidgetType | None:
        """The MVP widget type this request maps to, or None if unsupported/unspecified."""
        if self.chart_type is None:
            return None
        key = " ".join(self.chart_type.lower().replace("_", " ").split())
        try:
            return WidgetType(key.replace(" ", "_"))
        except ValueError:
            return CHART_TYPE_SYNONYMS.get(key)


class ScoreBreakdown(StrictModel):
    data_fit: float = Field(ge=0, le=1)
    intent: float = Field(ge=0, le=1)
    insight: float = Field(ge=0, le=1)
    perception: float = Field(ge=0, le=1)
    domain_prior: float = Field(ge=0, le=1)
    redundancy: float = Field(ge=0)
    clutter: float = Field(ge=0)
    total: float
    facts: list[str] = Field(default_factory=list)


class OptionStatus(StrEnum):
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    REQUESTED = "requested"
    NEEDS_APPROVAL = "needs_approval"
    EXCLUDED = "excluded"


class VisualOption(StrictModel):
    id: Identifier
    title: str = Field(min_length=1, max_length=200)
    chart_type: WidgetType
    fields: list[FieldName] = Field(default_factory=list)
    query: SemanticQuery
    measures: list[Measure] = Field(default_factory=list)
    encoding: Encoding = Field(default_factory=Encoding)
    options: WidgetOptions | None = None
    rationale: str = ""
    score: ScoreBreakdown | None = None
    status: OptionStatus
    request_id: str | None = None

    @model_validator(mode="after")
    def _measures_defined(self) -> Self:
        defined = {m.id for m in self.measures}
        if missing := sorted(set(self.query.measures) - defined):
            raise ValueError(f"option {self.id!r} uses undefined measure(s): {', '.join(missing)}")
        return self
