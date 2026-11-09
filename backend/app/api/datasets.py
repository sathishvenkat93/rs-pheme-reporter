"""Read access to registered datasets and their profiles."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, Caller
from app.db.tables import DatasetRow, DatasetVersionRow
from app.ingestion.catalog import to_model
from app.models.dataset import DatasetProfile, DatasetVersion
from app.profiling.service import latest_version, profile_version

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetOut(BaseModel):
    id: UUID
    name: str
    kind: str
    created_at: datetime
    versions: list[DatasetVersion]


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: UUID, db: DB, caller: Caller) -> DatasetOut:
    row = db.get(DatasetRow, dataset_id)
    if row is None or row.workspace_id != caller.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")
    versions = db.scalars(
        select(DatasetVersionRow)
        .where(DatasetVersionRow.dataset_id == dataset_id)
        .order_by(DatasetVersionRow.version)
    ).all()
    return DatasetOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        created_at=row.created_at,
        versions=[to_model(v) for v in versions],
    )


@router.get("/{dataset_id}/profile", response_model=DatasetProfile)
def get_profile(dataset_id: UUID, db: DB, caller: Caller) -> DatasetProfile:
    """Profile of the dataset's latest version (computed once, then cached)."""
    row = db.get(DatasetRow, dataset_id)
    if row is None or row.workspace_id != caller.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")
    version = latest_version(db, dataset_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset has no versions yet.")
    return profile_version(db, version)
