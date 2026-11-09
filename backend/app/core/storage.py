"""Object storage over the S3 API (SeaweedFS locally; any S3-compatible store works)."""

from __future__ import annotations

from functools import lru_cache

from minio import Minio

from app.core.config import get_settings


@lru_cache
def get_storage() -> Minio:
    """Return the process-wide MinIO client."""
    s = get_settings()
    return Minio(
        s.s3_endpoint,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key,
        secure=s.s3_secure,
    )


def ping() -> None:
    """Raise if storage is unreachable or the bucket is missing."""
    bucket = get_settings().s3_bucket
    if not get_storage().bucket_exists(bucket):
        raise LookupError(f"bucket {bucket!r} does not exist")
