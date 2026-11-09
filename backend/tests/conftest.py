"""Shared test setup.

Tests run against the docker-compose services but use their own database
(``pheme_test``), Redis DB 15 and bucket (``pheme-test``) so they never touch dev data.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Always override (never setdefault): tests flush Redis and reset tables, so they must
# never point at dev data. Use PHEME_TEST_* to redirect them.
_TEST_ENV = {
    # The app role (RLS applies) and the owner role (migrations, test housekeeping).
    "PHEME_DATABASE_URL": "postgresql+psycopg://pheme_app:pheme_app@localhost:5432/pheme_test",
    "PHEME_MIGRATION_DATABASE_URL": "postgresql+psycopg://pheme:pheme@localhost:5432/pheme_test",
    "PHEME_AUTH_MODE": "none",
    "PHEME_ENV": "test",
    "PHEME_REDIS_URL": "redis://localhost:6379/15",
    "PHEME_S3_BUCKET": "pheme-test",
    "PHEME_QUEUE_NAME": "pheme-test",
}
for _key, _default in _TEST_ENV.items():
    os.environ[_key] = os.environ.get(_key.replace("PHEME_", "PHEME_TEST_", 1), _default)

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core import redis as core_redis
from app.core import storage as core_storage
from app.core.config import get_settings


def _clear_caches() -> None:
    get_settings.cache_clear()
    core_db.get_engine.cache_clear()
    core_db._session_factory.cache_clear()
    core_redis.get_redis.cache_clear()
    core_storage.get_storage.cache_clear()


def _ensure_database() -> None:
    url = make_url(get_settings().migration_database_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def _ensure_bucket() -> None:
    client = core_storage.get_storage()
    bucket = get_settings().s3_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _migrate() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")


@pytest.fixture(scope="session", autouse=True)
def _services() -> Iterator[None]:
    _clear_caches()
    _ensure_database()
    _migrate()
    _ensure_bucket()
    yield
    core_db.get_engine().dispose()


@pytest.fixture
def redis_conn() -> Iterator[object]:
    conn = core_redis.get_redis()
    conn.flushdb()
    yield conn
    conn.flushdb()


def owner_engine() -> Engine:
    """Engine as the table owner (bypasses RLS): test housekeeping only."""
    return create_engine(get_settings().migration_database_url)
