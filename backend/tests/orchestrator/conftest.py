from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_llm
from app.orchestrator.llm import FakeLLM
from app.orchestrator.offline import offline_llm

from ..api.conftest import client as base_client  # noqa: F401 - re-exported fixture
from ..api.conftest import upload
from ..ingestion.helpers import FIXTURES, JUNIT


@pytest.fixture
def llm() -> FakeLLM:
    return offline_llm()


@pytest.fixture
def api(base_client: TestClient, llm: FakeLLM) -> Iterator[TestClient]:  # noqa: F811
    base_client.app.dependency_overrides[get_llm] = lambda: llm  # type: ignore[attr-defined]
    yield base_client
    base_client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def junit_session(api: TestClient, files: list[Path] | None = None) -> dict[str, Any]:
    """A session with the JUnit builds uploaded into one dataset, then proposed."""
    sid = api.post("/sessions").json()["id"]
    dataset_id = None
    for path in files or [FIXTURES / n for n in JUNIT]:
        form: dict[str, Any] = {"session_id": sid}
        if dataset_id:
            form["dataset_id"] = dataset_id
        status = upload(api, path, **form)
        assert status["status"] == "done", status
        dataset_id = status["dataset_ids"][0]
    session: dict[str, Any] = api.get(f"/sessions/{sid}").json()
    return session | {"dataset_id": dataset_id}


def say(api: TestClient, sid: str, text: str, **kw: Any) -> dict[str, Any]:
    resp = api.post(f"/sessions/{sid}/messages", json={"text": text, **kw})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body
