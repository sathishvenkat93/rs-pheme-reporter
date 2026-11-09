"""POST /query: the one safe path from a SemanticQuery to rows."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, Caller
from app.db.tables import DatasetRow, DatasetVersionRow
from app.models.dataset import ColumnSchema
from app.models.query import Measure, SemanticQuery
from app.query.compiler import QueryError
from app.query.executor import QueryResult, Target, execute

router = APIRouter(tags=["query"])


class QueryIn(BaseModel):
    dataset_id: UUID
    version: int | None = Field(default=None, ge=1, description="Defaults to the latest version")
    query: SemanticQuery
    measures: list[Measure] = Field(default_factory=list)


@router.post("/query", response_model=QueryResult)
def run_query(body: QueryIn, db: DB, caller: Caller) -> QueryResult:
    dataset = db.get(DatasetRow, body.dataset_id)
    if (
        dataset is None or dataset.workspace_id != caller.workspace_id
    ):  # checked before any cache lookup
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")
    stmt = select(DatasetVersionRow).where(DatasetVersionRow.dataset_id == dataset.id)
    stmt = (
        stmt.where(DatasetVersionRow.version == body.version)
        if body.version
        else stmt.order_by(DatasetVersionRow.version.desc())
    )
    version = db.scalars(stmt.limit(1)).first()
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset version not found.")
    target = Target(
        version_id=version.id,
        parts=version.parts,
        columns=[ColumnSchema.model_validate(c) for c in version.columns],
    )
    try:
        return execute(target, body.query, body.measures)
    except QueryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
