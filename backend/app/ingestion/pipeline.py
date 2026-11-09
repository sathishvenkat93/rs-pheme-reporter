"""The ingestion job: stored upload → detected → parsed → Parquet → registered dataset version."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.tables import DatasetRow, Upload
from app.ingestion import catalog, storage
from app.ingestion.detect import HEAD_BYTES, detect
from app.ingestion.errors import IngestionError
from app.ingestion.normalize import ParseContext, columns_of
from app.ingestion.parsers import parse, write_parquet

log = logging.getLogger(__name__)

GENERIC_FAILURE = "Something went wrong while reading this file. Please try again; if it keeps failing, contact support."


def ingest_upload(db: Session, upload_id: UUID) -> None:
    """Run ingestion for one upload and record the outcome on the Upload row."""
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise LookupError(f"upload {upload_id} not found")
    upload.status = "processing"
    db.commit()
    try:
        dataset_id = _ingest(db, upload)
    except IngestionError as exc:
        db.rollback()
        _fail(db, upload_id, exc.user_message)
        return
    except Exception:  # never leak internals to the user; keep the trace in server logs
        db.rollback()
        log.exception("ingestion failed for upload %s", upload_id)
        _fail(db, upload_id, GENERIC_FAILURE)
        return
    upload.status = "done"
    upload.error = None
    upload.dataset_ids = [str(dataset_id)]
    db.commit()


def _fail(db: Session, upload_id: UUID, message: str) -> None:
    upload = db.get(Upload, upload_id)
    if upload is not None:
        upload.status = "failed"
        upload.error = message
        db.commit()


def _ingest(db: Session, upload: Upload) -> UUID:
    settings = get_settings()
    detection = detect(upload.filename, storage.read_head(upload.object_key, HEAD_BYTES))
    upload.detected_format = detection.format.value

    dataset: DatasetRow | None = None
    prev = None
    if upload.target_dataset_id is not None:
        dataset = db.get(DatasetRow, upload.target_dataset_id)
        if dataset is None or dataset.workspace_id != upload.workspace_id:
            raise IngestionError("The dataset to add this file to doesn't exist.")
        prev = catalog.latest_version(db, dataset.id)

    with tempfile.TemporaryDirectory(prefix="pheme-ingest-") as tmp:
        src = Path(tmp) / storage.safe_name(upload.filename)
        storage.get_file(upload.object_key, src)
        ctx = ParseContext(
            upload_id=str(upload.id),
            filename=upload.filename,
            build=upload.build,
            build_order=(prev.version + 1) if prev else 1,
            default_tz=settings.default_timezone,
        )
        parsed = parse(src, detection, ctx)
        out = Path(tmp) / "part.parquet"
        schema, rows = write_parquet(parsed, out, detection.format)
        columns = columns_of(schema)

        if dataset is None:
            dataset = catalog.create_dataset(db, upload.workspace_id, upload.filename, parsed.kind)
        elif prev is not None:
            catalog.check_append(dataset, parsed.kind, prev, columns)

        key = storage.part_key(upload.workspace_id, dataset.id, upload.id)
        storage.put_file(key, out)
        catalog.add_version(db, dataset, prev, key, columns, rows, upload.id)
    return dataset.id
