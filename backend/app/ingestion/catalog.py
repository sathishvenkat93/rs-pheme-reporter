"""Dataset catalog: register datasets and their (append-mode) versions."""

from __future__ import annotations

from pathlib import PurePath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import DatasetRow, DatasetVersionRow
from app.ingestion.errors import IngestionError
from app.models.dataset import ColumnSchema, DatasetKind, DatasetVersion, is_superset, schema_hash


def latest_version(db: Session, dataset_id: UUID) -> DatasetVersionRow | None:
    return db.scalars(
        select(DatasetVersionRow)
        .where(DatasetVersionRow.dataset_id == dataset_id)
        .order_by(DatasetVersionRow.version.desc())
        .limit(1)
    ).first()


def create_dataset(db: Session, workspace_id: UUID, filename: str, kind: DatasetKind) -> DatasetRow:
    row = DatasetRow(
        workspace_id=workspace_id, name=PurePath(filename).stem[:200] or "dataset", kind=kind.value
    )
    db.add(row)
    db.flush()
    return row


def check_append(
    dataset: DatasetRow, kind: DatasetKind, prev: DatasetVersionRow, columns: list[ColumnSchema]
) -> None:
    """Refuse an append whose kind or columns don't fit the dataset (field mapping is M9)."""
    if dataset.kind != kind.value:
        raise IngestionError(
            f"This file holds {kind.value.replace('_', ' ')}, but the dataset holds "
            f"{dataset.kind.replace('_', ' ')}."
        )
    old = [ColumnSchema.model_validate(c) for c in prev.columns]
    if schema_hash(columns) != prev.schema_hash and not is_superset(columns, old):
        have = {(c.name, c.type) for c in columns}
        missing = sorted(c.name for c in old if (c.name, c.type) not in have)
        raise IngestionError(
            "This file's columns don't match the dataset. Missing or changed: "
            + ", ".join(missing[:10])
            + "."
        )


def add_version(
    db: Session,
    dataset: DatasetRow,
    prev: DatasetVersionRow | None,
    part: str,
    columns: list[ColumnSchema],
    rows: int,
    upload_id: UUID,
) -> DatasetVersion:
    """Create the next version: earlier parts plus ``part`` (append mode)."""
    row = DatasetVersionRow(
        workspace_id=dataset.workspace_id,
        dataset_id=dataset.id,
        version=(prev.version + 1) if prev else 1,
        parts=[*(prev.parts if prev else []), part],
        columns=[c.model_dump(mode="json") for c in columns],
        schema_hash=schema_hash(columns),
        row_count=(prev.row_count if prev else 0) + rows,
        upload_ids=[*(prev.upload_ids if prev else []), str(upload_id)],
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return to_model(row)


def to_model(row: DatasetVersionRow) -> DatasetVersion:
    return DatasetVersion.model_validate(
        {
            "id": row.id,
            "dataset_id": row.dataset_id,
            "version": row.version,
            "parts": row.parts,
            "columns": row.columns,
            "schema_hash": row.schema_hash,
            "row_count": row.row_count,
            "upload_ids": row.upload_ids,
            "created_at": row.created_at,
        }
    )
