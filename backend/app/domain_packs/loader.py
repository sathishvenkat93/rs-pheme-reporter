"""Domain packs: KPIs, signature visuals, synonyms and known derived metrics, as YAML."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from app.models.common import FieldName, Identifier, StrictModel
from app.models.query import Filter, Measure, SortKey
from app.models.spec import WidgetType

PACK_DIR = Path(__file__).parent


class PackMeasure(Measure):
    kpi: bool = False
    good: Literal["up", "down"] | None = None
    requires: list[FieldName] = Field(default_factory=list)


class SignatureVisual(StrictModel):
    id: Identifier
    title: str
    chart_type: WidgetType
    dimensions: list[FieldName] = Field(default_factory=list)
    measures: list[Identifier] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    sort: list[SortKey] = Field(default_factory=list)
    top_n: int | None = None
    time_grain: Literal["auto", "build"] | None = None
    distribution_of: FieldName | None = None  # box/histogram over a raw column
    requires: list[FieldName] = Field(default_factory=list)


class Derived(StrictModel):
    """Something users ask for that needs specific fields.

    ``kind: field`` is raw data the file lacks (e.g. CPU usage: the Field-exists check);
    ``kind: metric`` is computed from fields (e.g. MTTR: the Derivation check).
    """

    label: str
    needs: list[FieldName] = Field(min_length=1)
    kind: Literal["metric", "field"] = "metric"


class DomainPack(StrictModel):
    domain: str
    label: str
    measures: list[PackMeasure] = Field(default_factory=list)
    signature_visuals: list[SignatureVisual] = Field(default_factory=list)
    synonyms: dict[str, list[str]] = Field(
        default_factory=dict
    )  # column or measure id -> user words
    concepts: dict[str, list[str]] = Field(
        default_factory=dict
    )  # vague word -> candidate measure/column ids
    derived: dict[str, Derived] = Field(
        default_factory=dict
    )  # known metrics that need specific fields

    @model_validator(mode="after")
    def _refs(self) -> Self:
        ids = {m.id for m in self.measures}
        for v in self.signature_visuals:
            if missing := sorted(set(v.measures) - ids):
                raise ValueError(
                    f"{self.domain}: visual {v.id!r} uses undefined measure(s) {missing}"
                )
        return self

    @property
    def kpis(self) -> list[PackMeasure]:
        return [m for m in self.measures if m.kpi]

    def measure(self, measure_id: str) -> PackMeasure | None:
        return next((m for m in self.measures if m.id == measure_id), None)


@cache
def load_pack(domain: str) -> DomainPack:
    path = PACK_DIR / f"{domain}.yaml"
    if not path.exists():
        path = PACK_DIR / "generic.yaml"
    return DomainPack.model_validate(yaml.safe_load(path.read_text()))


def all_packs() -> list[DomainPack]:
    return [load_pack(p.stem) for p in sorted(PACK_DIR.glob("*.yaml"))]
