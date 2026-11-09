from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import new_session
from app.db.tables import Profile

from ..ingestion.helpers import FIXTURES, JUNIT
from .conftest import upload


def test_profile_endpoint_profiles_latest_version_and_caches(client: TestClient) -> None:
    (dataset_id,) = upload(client, FIXTURES / JUNIT[0])["dataset_ids"]
    first = client.get(f"/datasets/{dataset_id}/profile")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["domain"] == "test_execution"
    assert body["row_count"] > 0

    assert client.get(f"/datasets/{dataset_id}/profile").json() == body
    with new_session() as db:
        assert db.scalar(select(func.count()).select_from(Profile)) == 1

    upload(client, FIXTURES / JUNIT[1], dataset_id=dataset_id)  # new version → new profile
    second = client.get(f"/datasets/{dataset_id}/profile").json()
    assert second["dataset_version_id"] != body["dataset_version_id"]
    assert second["row_count"] > body["row_count"]


def test_profile_of_unknown_dataset_is_404(client: TestClient) -> None:
    assert client.get("/datasets/6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a/profile").status_code == 404
