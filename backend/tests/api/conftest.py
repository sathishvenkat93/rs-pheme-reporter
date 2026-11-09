from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from rq import SimpleWorker
from sqlalchemy import text

from app.core.db import get_engine
from app.core.redis import get_queue
from app.db.tables import Base
from app.main import create_app


@pytest.fixture
def client(redis_conn: object) -> Iterator[TestClient]:
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    with TestClient(create_app()) as c:
        yield c


def run_jobs() -> None:
    queue = get_queue()
    SimpleWorker([queue], connection=queue.connection).work(burst=True)


def upload(client: TestClient, path: Path, **form: Any) -> dict[str, Any]:
    """Upload a file, run the queued ingestion, and return the final upload status."""
    with path.open("rb") as fh:
        resp = client.post(
            "/uploads", files={"file": (path.name, fh)}, data={k: str(v) for k, v in form.items()}
        )
    assert resp.status_code == 202, resp.text
    run_jobs()
    status = client.get(f"/uploads/{resp.json()['id']}")
    assert status.status_code == 200
    body: dict[str, Any] = status.json()
    return body
