"""RQ entrypoint for ingestion."""

from __future__ import annotations

from uuid import UUID

from app.core.db import new_session
from app.ingestion.pipeline import ingest_upload


def run(upload_id: str) -> None:
    with new_session(bypass=True) as db:  # system work: the upload row says which workspace
        ingest_upload(db, UUID(upload_id))
