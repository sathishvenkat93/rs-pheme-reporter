"""The Dashboard Spec: the single source of truth for a dashboard (design rule 2)."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.models.common import FieldName, Identifier, Scalar, StrictModel
from app.models.dataset import DatasetKind
from app.models.query import Measure, SemanticQuery

GRID_COLUMNS = 12


class WidgetType(StrEnum):
    KPI = "kpi"
    LINE = "line"
    AREA = "area"
    BAR = "bar"
    STACKED_BAR = "stacked_bar"
    DONUT = "donut"
    HISTOGRAM = "histogram"
    BOX = "box"
    SCATTER = "scatter"
    HEATMAP = "heatmap"
    PARETO = "pareto"
    TABLE = "table"


StatusColor = Literal["good", "warning", "critical"]


class DatasetBinding(StrictModel):
    """A dataset used by the spec. Snapshot mode pins a version; live follows the latest."""

    id: Identifier
    dataset_id: UUID
    kind: DatasetKind
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["snapshot", "live"]
    version_id: UUID | None = None

    @model_validator(mode="after")
    def _pinning(self) -> Self:
        if self.mode == "snapshot" and self.version_id is None:
            raise ValueError("snapshot mode needs version_id")
        if self.mode == "live" and self.version_id is not None:
            raise ValueError("live mode follows the latest version; version_id must be null")
        return self


class GlobalFilter(StrictModel):
    id: Identifier
    field: FieldName
    control: Literal["multiselect", "select", "range", "date_range"]
    dataset: Identifier | None = None
    label: str | None = None


class Encoding(StrictModel):
    x: FieldName | None = None
    y: FieldName | None = None
    series: FieldName | None = None
    color: FieldName | None = None
    size: FieldName | None = None
    value: FieldName | None = None


class WidgetOptions(StrictModel):
    stack: Literal["none", "stacked", "percent"] | None = None
    horizontal: bool | None = None
    bins: int | None = Field(default=None, gt=0, le=500)
    log_scale: bool | None = None
    sparkline: bool | None = None
    show_other: bool | None = None


class Threshold(StrictModel):
    below: float | None = None
    above: float | None = None
    color: StatusColor

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if (self.below is None) == (self.above is None):
            raise ValueError("a threshold needs exactly one of below or above")
        return self


class Annotation(StrictModel):
    type: Literal["rule", "band", "text"]
    x: Scalar | None = None
    x_end: Scalar | None = None
    y: float | None = None
    y_end: float | None = None
    label: str | None = None

    @model_validator(mode="after")
    def _anchored(self) -> Self:
        if self.x is None and self.y is None:
            raise ValueError("an annotation needs x or y")
        return self


class Provenance(StrictModel):
    option_id: str
    rationale: str | None = None
    decision_ids: list[str] = Field(default_factory=list)


class Widget(StrictModel):
    id: Identifier
    type: WidgetType
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    query: SemanticQuery
    encoding: Encoding | None = None
    options: WidgetOptions | None = None
    target: float | None = None
    thresholds: list[Threshold] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    provenance: Provenance | None = None


class LayoutItem(StrictModel):
    widget: Identifier
    x: int = Field(ge=0, lt=GRID_COLUMNS)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=GRID_COLUMNS)
    h: int = Field(ge=1, le=48)

    @model_validator(mode="after")
    def _fits(self) -> Self:
        if self.x + self.w > GRID_COLUMNS:
            raise ValueError(f"layout item for {self.widget!r} overflows the 12-column grid")
        return self


class Layout(StrictModel):
    columns: Literal[12] = 12
    items: list[LayoutItem] = Field(default_factory=list)


def _dupes(values: list[str]) -> list[str]:
    return sorted(v for v, n in Counter(values).items() if n > 1)


class DashboardSpec(StrictModel):
    """Spec body. The version number lives on DashboardVersion, not here."""

    dashboard_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    datasets: list[DatasetBinding] = Field(min_length=1)
    filters: list[GlobalFilter] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    layout: Layout = Field(default_factory=Layout)
    widgets: list[Widget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        for label, ids in (
            ("widget", [w.id for w in self.widgets]),
            ("measure", [m.id for m in self.measures]),
            ("filter", [f.id for f in self.filters]),
            ("dataset", [d.id for d in self.datasets]),
            ("layout item", [i.widget for i in self.layout.items]),
        ):
            if d := _dupes(ids):
                raise ValueError(f"duplicate {label} id(s): {', '.join(d)}")

        widget_ids = {w.id for w in self.widgets}
        laid_out = {i.widget for i in self.layout.items}
        if missing := sorted(laid_out - widget_ids):
            raise ValueError(f"layout references missing widget(s): {', '.join(missing)}")
        if unplaced := sorted(widget_ids - laid_out):
            raise ValueError(f"widget(s) without a layout item: {', '.join(unplaced)}")

        dataset_ids = {d.id for d in self.datasets}
        measure_ids = {m.id for m in self.measures}
        for w in self.widgets:
            if w.query.dataset not in dataset_ids:
                raise ValueError(f"widget {w.id!r} uses unknown dataset {w.query.dataset!r}")
            if unknown := sorted(set(w.query.measures) - measure_ids):
                raise ValueError(f"widget {w.id!r} uses undefined measure(s): {', '.join(unknown)}")
        for f in self.filters:
            if f.dataset is not None and f.dataset not in dataset_ids:
                raise ValueError(f"filter {f.id!r} uses unknown dataset {f.dataset!r}")
        return self
