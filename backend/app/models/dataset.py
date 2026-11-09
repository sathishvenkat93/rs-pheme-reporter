"""Datasets, their versions and profiles."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from app.models.common import FieldName, StrictModel


class ColumnRole(StrEnum):
    TIME = "time"
    MEASURE = "measure"
    COUNT = "count"
    DURATION = "duration"
    RATIO = "ratio"
    CATEGORY = "category"
    IDENTIFIER = "identifier"
    STATUS = "status"
    TEXT = "text"
    GEO = "geo"
    HIERARCHY_LEVEL = "hierarchy_level"


class ColumnType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    DATE = "date"


class DatasetKind(StrEnum):
    TEST_RESULTS = "test_results"
    PERF_SAMPLES = "perf_samples"
    GENERIC = "generic"


class ColumnSchema(StrictModel):
    name: FieldName
    type: ColumnType


def schema_hash(columns: list[ColumnSchema]) -> str:
    """Stable hash of column names and types, independent of column order."""
    canon = "\n".join(f"{c.name}:{c.type.value}" for c in sorted(columns, key=lambda c: c.name))
    return hashlib.sha256(canon.encode()).hexdigest()


def is_superset(new: list[ColumnSchema], old: list[ColumnSchema]) -> bool:
    """True when ``new`` has every column of ``old`` with the same type."""
    have = {(c.name, c.type) for c in new}
    return all((c.name, c.type) in have for c in old)


class Dataset(StrictModel):
    id: UUID
    workspace_id: UUID
    name: str = Field(min_length=1, max_length=200)
    kind: DatasetKind
    created_at: datetime


class DatasetVersion(StrictModel):
    """One immutable version. In append mode it holds all earlier parts plus the new one."""

    id: UUID
    dataset_id: UUID
    version: int = Field(ge=1)
    parts: list[str] = Field(min_length=1)
    columns: list[ColumnSchema] = Field(min_length=1)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=0)
    upload_ids: list[UUID] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("duplicate column names")
        if self.schema_hash != schema_hash(self.columns):
            raise ValueError("schema_hash does not match columns")
        return self


# ------------------------------------------------------------------ profiles (filled in M4)


class NumericStats(StrictModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None
    skew: float | None = None


class TopValue(StrictModel):
    value: str
    count: int = Field(ge=0)


class TimeStats(StrictModel):
    span_seconds: float = Field(ge=0)
    median_gap_seconds: float | None = Field(default=None, ge=0)
    monotonic: bool


class ColumnProfile(StrictModel):
    name: FieldName
    type: ColumnType
    role: ColumnRole
    null_pct: float = Field(ge=0, le=100)
    distinct: int = Field(ge=0)
    numeric: NumericStats | None = None
    top_k: list[TopValue] = Field(default_factory=list)
    time: TimeStats | None = None


class Correlation(StrictModel):
    a: FieldName
    b: FieldName
    r: float = Field(ge=-1, le=1)


class Trend(StrictModel):
    measure: FieldName
    strength: float = Field(ge=0, le=1)
    direction: int = Field(ge=-1, le=1)


class ChangePoint(StrictModel):
    measure: FieldName
    at: str


class DatasetProfile(StrictModel):
    dataset_version_id: UUID
    kind: DatasetKind
    row_count: int = Field(ge=0)
    columns: list[ColumnProfile]
    grain: str | None = None
    candidate_keys: list[list[FieldName]] = Field(default_factory=list)
    hierarchies: list[list[FieldName]] = Field(default_factory=list)
    correlations: list[Correlation] = Field(default_factory=list)
    outliers: dict[str, int] = Field(default_factory=dict)
    trends: list[Trend] = Field(default_factory=list)
    change_points: list[ChangePoint] = Field(default_factory=list)
    domain: str | None = None
    domain_confidence: float | None = Field(default=None, ge=0, le=1)
