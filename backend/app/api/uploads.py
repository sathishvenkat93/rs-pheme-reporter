"""Uploads: stream to the object store, then ingest in the background."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.api.deps import DB, Caller
from app.core.config import get_settings
from app.core.redis import get_queue
from app.db.tables import DatasetRow, SessionRow, Upload, Workspace
from app.ingestion import storage

router = APIRouter(prefix="/uploads", tags=["uploads"])


class UploadOut(BaseModel):
    id: UUID
    status: str
    filename: str
    size_bytes: int
    sha256: str
    detected_format: str | None
    error: str | None
    dataset_ids: list[UUID]


def _out(row: Upload) -> UploadOut:
    return UploadOut(
        id=row.id,
        status=row.status,
        filename=row.filename,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        detected_format=row.detected_format,
        error=row.error,
        dataset_ids=[UUID(d) for d in row.dataset_ids],
    )


def _too_large(limit: int) -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"The file is larger than the {limit // (1024 * 1024)} MB limit.",
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadOut)
def create_upload(
    request: Request,
    db: DB,
    caller: Caller,
    file: Annotated[UploadFile, File()],
    session_id: Annotated[UUID | None, Form()] = None,
    dataset_id: Annotated[UUID | None, Form()] = None,
    build: Annotated[str | None, Form(max_length=200)] = None,
) -> UploadOut:
    workspace = db.get(Workspace, caller.workspace_id)
    limit = (
        workspace.max_upload_bytes if workspace and workspace.max_upload_bytes else None
    ) or get_settings().max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 64 * 1024:
        raise _too_large(limit)
    if session_id is not None:
        owner = db.get(SessionRow, session_id)
        if owner is None or owner.workspace_id != caller.workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    if dataset_id is not None:
        target = db.get(DatasetRow, dataset_id)
        if target is None or target.workspace_id != caller.workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")

    upload_id = uuid4()
    filename = file.filename or "upload"
    key = storage.upload_key(caller.workspace_id, upload_id, filename)
    try:
        size, sha = storage.put_stream(key, file.file, limit)
    except storage.TooLarge:
        raise _too_large(limit) from None

    row = Upload(
        id=upload_id,
        workspace_id=caller.workspace_id,
        session_id=session_id,
        created_by=caller.user_id,
        filename=filename,
        content_type=file.content_type,
        size_bytes=size,
        sha256=sha,
        object_key=key,
        status="pending",
        build=build or None,
        target_dataset_id=dataset_id,
    )
    db.add(row)
    db.commit()
    get_queue().enqueue("app.jobs.ingest.run", str(upload_id), job_timeout=1800)
    return _out(row)


@router.get("/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: UUID, db: DB, caller: Caller) -> UploadOut:
    row = db.get(Upload, upload_id)
    if row is None or row.workspace_id != caller.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found.")
    db.refresh(row)
    return _out(row)
