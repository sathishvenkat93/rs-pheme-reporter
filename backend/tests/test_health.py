from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.main import create_app


def test_health_reports_ok_for_every_dependency() -> None:
    resp = TestClient(create_app()).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "redis": "ok", "object_store": "ok"},
    }


def test_health_is_503_when_a_dependency_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise ConnectionError("down")

    monkeypatch.setitem(health.CHECKS, "redis", broken)
    resp = TestClient(create_app()).get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "error: ConnectionError"
    assert body["checks"]["postgres"] == "ok"
