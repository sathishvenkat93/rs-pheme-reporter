"""Shared base model and constrained types."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
"""Spec-local ids (widgets, measures, filters, dataset aliases) and column names."""

FieldName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
"""A column name in a dataset."""

Scalar = str | int | float | bool


class StrictModel(BaseModel):
    """Base for every domain model: unknown keys rejected, invariants checked by validators.

    Type checking is Pydantic's default (lax) mode rather than ``strict=True``: FastAPI
    validates request bodies in Python mode, where strict mode rejects UUIDs, datetimes
    and enums given as JSON strings. Unions use smart mode, so ``1`` and ``"1"`` stay
    distinct.
    """

    model_config = ConfigDict(extra="forbid", validate_default=True)
