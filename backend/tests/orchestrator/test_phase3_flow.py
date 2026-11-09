"""Phase 3 exit: the whole flow through the API with FakeLLM (offline policy), on JUnit.

session → upload → options → flagged request → resolve → approve → build → chat edit →
undo → second upload refreshes the live dashboard.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ..api.conftest import upload
from ..ingestion.helpers import FIXTURES, JUNIT
from .conftest import say


def test_full_api_flow(api: TestClient) -> None:
    # 1. Session + upload (four builds, one dataset)
    sid = api.post("/sessions").json()["id"]
    dataset_id = None
    for name in JUNIT[:4]:
        form = {"session_id": sid} | ({"dataset_id": dataset_id} if dataset_id else {})
        status = upload(api, FIXTURES / name, **form)
        assert status["status"] == "done"
        dataset_id = status["dataset_ids"][0]
    api.post(
        f"/sessions/{sid}/context", json={"text": "pass rate should be at least 95 %, env=staging"}
    )

    # 2. Options list
    s = api.get(f"/sessions/{sid}").json()
    assert s["state"] == "PROPOSE"
    plan = api.get(f"/plans/{s['plan_id']}").json()
    assert sum(o["status"] == "recommended" for o in plan["options"]) >= 6

    # 3. A request that gets flagged
    reply = say(api, sid, "Show a pie of failures by test")
    assert "adjusted" in reply["reply"]
    decisions = api.get(f"/plans/{s['plan_id']}/decisions").json()
    (open_decision,) = [d for d in decisions if d["choice"] is None]
    assert api.post(f"/sessions/{sid}/approve").status_code == 409

    # 4. Resolve, approve (the user's click), build
    assert (
        api.post(
            f"/decisions/{open_decision['id']}", json={"choice": "accept_alternative"}
        ).status_code
        == 200
    )
    assert api.post(f"/sessions/{sid}/approve").json()["state"] == "APPROVED"
    built = say(api, sid, "build it")
    assert built["state"] == "PUBLISHED"
    dash = api.get(f"/sessions/{sid}").json()["dashboard_id"]
    v1 = api.get(f"/dashboards/{dash}").json()
    assert all(
        isinstance(r, dict) and r["rows"]
        for r in api.get(f"/dashboards/{dash}/data").json().values()
    )
    kpi = next(w for w in v1["spec"]["widgets"] if w["id"] == "w_kpi_pass_rate")
    assert kpi["target"] == 0.95  # "at least 95 %" from the context note, as a 0-1 ratio

    # 5. Edit via chat, preview, apply
    say(api, sid, "rename the pass rate by build to Build health")
    assert api.get(f"/dashboards/{dash}").json()["version"] == 1
    assert api.post(f"/sessions/{sid}/edit/apply").status_code == 200
    v2 = api.get(f"/dashboards/{dash}").json()
    assert v2["version"] == 2
    assert any(w["title"] == "Build health" for w in v2["spec"]["widgets"])

    # 6. Undo
    undo = say(api, sid, "undo")
    assert "Restored version 1" in undo["reply"]
    v3 = api.get(f"/dashboards/{dash}").json()
    assert v3["version"] == 3
    assert json.dumps(v3["spec"], sort_keys=True) == json.dumps(v1["spec"], sort_keys=True)

    # 7. Refresh with a second upload
    trend = "w_sig_pass_rate_by_build"
    before = [r[0] for r in api.get(f"/dashboards/{dash}/data").json()[trend]["rows"]]
    status = upload(api, FIXTURES / JUNIT[4], dataset_id=dataset_id)
    refreshed = api.post(f"/dashboards/{dash}/refresh", json={"upload_id": status["id"]}).json()
    assert refreshed["status"] == "refreshed"
    after = [r[0] for r in api.get(f"/dashboards/{dash}/data").json()[trend]["rows"]]
    assert after == [*before, "105"]
    assert api.get(f"/sessions/{sid}").json()["state"] == "PUBLISHED"

    # Everything is versioned and audited
    assert [v["version"] for v in api.get(f"/dashboards/{dash}/versions").json()] == [1, 2, 3]
