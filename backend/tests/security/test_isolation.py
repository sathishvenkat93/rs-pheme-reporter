"""M12: workspace isolation (API and database), dashboard roles, auth modes and the audit trail."""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.api.deps import get_llm
from app.core.auth import AuthError, check_startup, verify_oidc, workspace_id_for
from app.core.config import get_settings
from app.main import create_app
from app.orchestrator.offline import offline_llm

from ..api.conftest import upload
from ..ingestion.helpers import FIXTURES, JUNIT
from ..orchestrator.test_dashboards import build_dashboard


@pytest.fixture
def dev_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # client: clean tables
    monkeypatch.setattr(get_settings(), "auth_mode", "dev")


def login(email: str) -> TestClient:
    app = create_app()
    llm = offline_llm()
    app.dependency_overrides[get_llm] = lambda: llm
    anon = TestClient(app)
    token = anon.post("/auth/login", json={"email": email}).json()["token"]
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.usefixtures("dev_auth")
def test_requests_without_a_token_are_401() -> None:
    anon = TestClient(create_app())
    assert anon.post("/sessions").status_code == 401
    assert anon.get("/dashboards").status_code == 401
    assert anon.get("/dashboards", headers={"Authorization": "Bearer nonsense"}).status_code == 401


@pytest.mark.usefixtures("dev_auth")
def test_login_provisions_users_into_workspaces_by_domain() -> None:
    a = login("Ann@Alpha.test")
    me = a.get("/auth/me").json()
    assert me["email"] == "ann@alpha.test"
    assert me["workspace_id"] == str(workspace_id_for("alpha.test"))
    assert login("cy@alpha.test").get("/auth/me").json()["workspace_id"] == me["workspace_id"]
    assert login("bo@beta.test").get("/auth/me").json()["workspace_id"] != me["workspace_id"]


@pytest.mark.usefixtures("dev_auth")
def test_workspace_b_cannot_read_or_change_workspace_a() -> None:
    a = login("ann@alpha.test")
    ids = build_dashboard(a)
    sid, dash, dataset = ids["sid"], ids["dash"], ids["dataset_id"]
    plan_id = a.get(f"/sessions/{sid}").json()["plan_id"]
    decision = a.get(f"/plans/{plan_id}/decisions").json()[0]["id"]
    upload_id = upload(a, FIXTURES / JUNIT[4], dataset_id=dataset)["id"]

    b = login("bo@beta.test")
    reads = [
        f"/sessions/{sid}", f"/sessions/{sid}/messages", f"/sessions/{sid}/events?follow=false", f"/plans/{plan_id}",
        f"/plans/{plan_id}/decisions", f"/dashboards/{dash}", f"/dashboards/{dash}/versions", f"/dashboards/{dash}/data",
        f"/dashboards/{dash}/versions/1", f"/dashboards/{dash}/audit", f"/dashboards/{dash}/shares",
        f"/datasets/{dataset}", f"/datasets/{dataset}/profile", f"/uploads/{upload_id}",
    ]  # fmt: skip
    for url in reads:
        assert b.get(url).status_code == 404, url
    assert b.get("/dashboards").json() == []
    q = {
        "dataset_id": dataset,
        "query": {"dataset": "ds", "measures": ["n"]},
        "measures": [{"id": "n", "expr": "count()"}],
    }
    writes: list[tuple[str, str, dict[str, Any] | None]] = [
        ("post", f"/sessions/{sid}/messages", {"text": "drop everything"}),
        ("post", f"/sessions/{sid}/approve", None),
        ("post", f"/decisions/{decision}", {"choice": "drop"}),
        ("patch", f"/dashboards/{dash}", {"base_version": 1, "ops": [{"op": "replace", "path": "/title", "value": "pwned"}]}),
        ("post", f"/dashboards/{dash}/restore/1", None),
        ("post", f"/dashboards/{dash}/refresh", {"upload_id": upload_id}),
        ("delete", f"/dashboards/{dash}", None),
        ("post", "/query", q),
    ]  # fmt: skip
    for method, url, body in writes:
        resp = getattr(b, method)(url, json=body) if body is not None else getattr(b, method)(url)
        assert resp.status_code == 404, (method, url, resp.status_code)
    with (FIXTURES / JUNIT[0]).open("rb") as fh:  # can't append to A's dataset either
        assert (
            b.post(
                "/uploads", files={"file": ("x.xml", fh)}, data={"dataset_id": dataset}
            ).status_code
            == 404
        )
    assert a.get(f"/dashboards/{dash}").json()["spec"]["title"] != "pwned"


@pytest.mark.usefixtures("dev_auth")
def test_database_policies_enforce_isolation_for_the_app_role() -> None:
    a = login("ann@alpha.test")
    build_dashboard(a)
    ws_a, ws_b = workspace_id_for("alpha.test"), workspace_id_for("beta.test")
    login("bo@beta.test")
    engine = create_engine(get_settings().database_url)  # the pheme_app role, as the app uses
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT current_user")).scalar() == "pheme_app"
            conn.execute(text("SELECT set_config('app.workspace_id', :w, true)"), {"w": str(ws_a)})
            assert conn.execute(text("SELECT count(*) FROM dashboards")).scalar() == 1
            assert (conn.execute(text("SELECT count(*) FROM datasets")).scalar() or 0) >= 1
        with engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.workspace_id', :w, true)"), {"w": str(ws_b)})
            for table in (
                "dashboards",
                "dashboard_versions",
                "datasets",
                "sessions",
                "messages",
                "uploads",
                "audit_events",
            ):
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
        with engine.begin() as conn:  # no workspace set at all: nothing visible
            assert conn.execute(text("SELECT count(*) FROM dashboards")).scalar() == 0
        insert = text(
            "INSERT INTO datasets (id, workspace_id, name, kind) VALUES (gen_random_uuid(), :w, 'x', 'generic')"
        )
        with engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.workspace_id', :w, true)"), {"w": str(ws_b)})
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(insert, {"w": str(ws_a)})
        with engine.begin() as conn:  # the app role can't switch RLS off
            conn.execute(text("SELECT set_config('app.workspace_id', :w, true)"), {"w": str(ws_b)})
            with pytest.raises(DBAPIError):
                conn.execute(text("ALTER TABLE dashboards DISABLE ROW LEVEL SECURITY"))
    finally:
        engine.dispose()


@pytest.mark.usefixtures("dev_auth")
def test_dashboard_roles_viewer_editor_owner() -> None:
    owner = login("ann@alpha.test")
    dash = build_dashboard(owner)["dash"]
    colleague = login("cy@alpha.test")
    patch = {"base_version": 1, "ops": [{"op": "replace", "path": "/title", "value": "Edited"}]}

    assert colleague.get(f"/dashboards/{dash}").status_code == 200  # workspace members can view
    assert colleague.patch(f"/dashboards/{dash}", json=patch).status_code == 403
    assert colleague.post(f"/dashboards/{dash}/restore/1").status_code == 403
    assert (
        colleague.post(
            f"/dashboards/{dash}/shares", json={"email": "cy@alpha.test", "role": "editor"}
        ).status_code
        == 403
    )

    shared = owner.post(
        f"/dashboards/{dash}/shares", json={"email": "cy@alpha.test", "role": "editor"}
    )
    assert shared.status_code == 200
    assert shared.json() == [
        {
            "user_id": colleague.get("/auth/me").json()["user_id"],
            "email": "cy@alpha.test",
            "role": "editor",
        }
    ]
    assert colleague.patch(f"/dashboards/{dash}", json=patch).status_code == 200
    assert (
        colleague.post(
            f"/dashboards/{dash}/shares", json={"email": "ann@alpha.test", "role": "viewer"}
        ).status_code
        == 403
    )
    assert colleague.delete(f"/dashboards/{dash}").status_code == 403
    assert (
        owner.post(
            f"/dashboards/{dash}/shares", json={"email": "bo@beta.test", "role": "viewer"}
        ).status_code
        == 404
    )

    # Chat edits respect the role too
    viewer_session = login("di@alpha.test").post("/sessions", json={"dashboard_id": dash}).json()
    assert viewer_session["state"] == "PUBLISHED"

    assert owner.delete(f"/dashboards/{dash}").status_code == 204
    assert owner.get(f"/dashboards/{dash}").status_code == 404
    assert owner.get("/dashboards").json() == []


@pytest.mark.usefixtures("dev_auth")
def test_audit_trail_records_tool_calls_approvals_decisions_and_patches() -> None:
    owner = login("ann@alpha.test")
    dash = build_dashboard(owner)["dash"]
    owner.patch(
        f"/dashboards/{dash}",
        json={"base_version": 1, "ops": [{"op": "replace", "path": "/title", "value": "T"}]},
    )
    owner.post(f"/dashboards/{dash}/restore/1")
    actions = [e["action"] for e in owner.get(f"/dashboards/{dash}/audit").json()]
    for expected in ("tool.parse_chart_request", "tool.check_feasibility", "decision.resolve", "plan.approve",
                     "tool.build_dashboard", "dashboard.build", "dashboard.patch", "dashboard.restore"):  # fmt: skip
        assert expected in actions, expected


def test_oidc_tokens_are_verified_against_the_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(get_settings(), "oidc_issuer", "https://idp.example.test")
    monkeypatch.setattr(get_settings(), "oidc_audience", "pheme")

    class Jwks:
        def get_signing_key_from_jwt(self, token: str) -> Any:
            return type("K", (), {"key": key.public_key()})()

    def token(signer: Any = key, **over: Any) -> str:
        claims = {"iss": "https://idp.example.test", "aud": "pheme", "exp": int(time.time()) + 60,
                  "email": "eve@corp.test"} | over  # fmt: skip
        return jwt.encode(claims, signer, algorithm="RS256")

    assert verify_oidc(token(), Jwks())["email"] == "eve@corp.test"
    for bad in (
        token(signer=other),
        token(aud="someone-else"),
        token(iss="https://evil.test"),
        token(exp=int(time.time()) - 5),
    ):
        with pytest.raises(AuthError):
            verify_oidc(bad, Jwks())


def test_production_refuses_the_dev_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "env", "production")
    with pytest.raises(RuntimeError, match="PHEME_AUTH_MODE=none"):
        check_startup()
    monkeypatch.setattr(get_settings(), "auth_mode", "dev")
    with pytest.raises(RuntimeError, match="PHEME_SECRET_KEY"):
        check_startup()
