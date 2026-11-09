"""Object-store helpers for ingestion: streamed uploads with hashing and a size cap."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from app.core.config import get_settings
from app.core.storage import get_storage

PART_SIZE = 10 * 1024 * 1024


class TooLarge(Exception):
    """The upload exceeded the configured size limit."""


class _HashingReader:
    """File-like wrapper that hashes what it reads and enforces a byte limit."""

    def __init__(self, src: BinaryIO, limit: int) -> None:
        self.src = src
        self.limit = limit
        self.size = 0
        self.sha = hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        chunk = self.src.read(n)
        self.size += len(chunk)
        if self.size > self.limit:
            raise TooLarge
        self.sha.update(chunk)
        return chunk


def safe_name(filename: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "upload"
    return name[:200]


def upload_key(workspace_id: UUID, upload_id: UUID, filename: str) -> str:
    return f"ws/{workspace_id}/uploads/{upload_id}/{safe_name(filename)}"


def part_key(workspace_id: UUID, dataset_id: UUID, upload_id: UUID) -> str:
    return f"ws/{workspace_id}/datasets/{dataset_id}/parts/{upload_id}.parquet"


def put_stream(key: str, src: BinaryIO, limit: int) -> tuple[int, str]:
    """Stream ``src`` to ``key`` as a multipart upload. Returns (size, sha256 hex)."""
    reader = _HashingReader(src, limit)
    get_storage().put_object(
        get_settings().s3_bucket,
        key,
        reader,  # type: ignore[arg-type]  # duck-typed: the client only calls .read()
        -1,
        part_size=PART_SIZE,
    )
    return reader.size, reader.sha.hexdigest()


def put_file(key: str, path: Path) -> None:
    get_storage().fput_object(get_settings().s3_bucket, key, str(path), part_size=PART_SIZE)


def get_file(key: str, dest: Path) -> None:
    get_storage().fget_object(get_settings().s3_bucket, key, str(dest))


def read_head(key: str, n: int) -> bytes:
    resp = get_storage().get_object(get_settings().s3_bucket, key, offset=0, length=n)
    try:
        return bytes(resp.read())
    finally:
        resp.close()
        resp.release_conn()
