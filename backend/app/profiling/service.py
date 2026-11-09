"""Profile a stored dataset version, caching the result in the ``profiles`` table."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import DatasetRow, DatasetVersionRow, Profile
from app.models.dataset import DatasetKind, DatasetProfile
from app.profiling.profiler import profile_paths
from app.query.engine import local_parts


def profile_version(db: Session, version: DatasetVersionRow) -> DatasetProfile:
    cached = db.scalars(select(Profile).where(Profile.dataset_version_id == version.id)).first()
    if cached is not None:
        return DatasetProfile.model_validate(cached.profile)
    dataset = db.get(DatasetRow, version.dataset_id)
    assert dataset is not None
    profile = profile_paths(local_parts(version.parts), DatasetKind(dataset.kind), version.id)
    db.add(
        Profile(
            workspace_id=version.workspace_id,
            dataset_version_id=version.id,
            profile=profile.model_dump(mode="json"),
        )
    )
    db.commit()
    return profile


def latest_version(db: Session, dataset_id: UUID) -> DatasetVersionRow | None:
    return db.scalars(
        select(DatasetVersionRow)
        .where(DatasetVersionRow.dataset_id == dataset_id)
        .order_by(DatasetVersionRow.version.desc())
    ).first()
