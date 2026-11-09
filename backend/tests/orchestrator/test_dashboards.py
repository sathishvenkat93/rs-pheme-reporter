from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.orchestrator.llm import FakeLLM, FakeRule, tool_response

from ..api.conftest import upload
from ..ingestion.helpers import FIXTURES, JUNIT
from .conftest import junit_session, say


def build_dashboard(api: TestClient, builds: int = 4) -> dict[str, Any]:
    """JUnit session → flagged pie → resolve → approve → build. Returns ids."""
    s = junit_session(api, [FIXTURES / n for n in JUNIT[:builds]])
    sid = s["id"]
    say(api, sid, "Show a pie of failures by test")
    plan = api.get(f"/plans/{api.get(f'/sessions/{sid}').json()['plan_id']}").json()
    decision = next(d for d in plan["decisions"] if d["choice"] is None)
    assert (
        api.post(f"/decisions/{decision['id']}", json={"choice": "accept_alternative"}).status_code
        == 200
    )
    assert api.post(f"/sessions/{sid}/approve").status_code == 200
    reply = say(api, sid, "Looks good, build it")
    assert reply["state"] == "PUBLISHED", reply
    dash_id = api.get(f"/sessions/{sid}").json()["dashboard_id"]
    assert dash_id
    return {"sid": sid, "dash": dash_id, "dataset_id": s["dataset_id"]}


def spec(api: TestClient, dash: str, n: int | None = None) -> dict[str, Any]:
    url = f"/dashboards/{dash}" + (f"/versions/{n}" if n else "")
    body: dict[str, Any] = api.get(url).json()
    return body


def widget(s: dict[str, Any], pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    return next(w for w in s["spec"]["widgets"] if pred(w))


def test_build_yields_a_valid_spec_where_every_widget_returns_data(api: TestClient) -> None:
    ids = build_dashboard(api)
    v1 = spec(api, ids["dash"])
    assert v1["version"] == 1
    widgets = v1["spec"]["widgets"]
    assert 6 <= len(widgets) <= 11
    assert any(
        w["provenance"]["decision_ids"] for w in widgets
    )  # the resolved request is traceable
    types = {w["id"]: w["type"] for w in v1["spec"]["widgets"]}
    kpis = [i for i in v1["spec"]["layout"]["items"] if types[i["widget"]] == "kpi"]
    assert all(i["w"] == 3 and i["y"] in (0, 2) for i in kpis)
    data = api.get(f"/dashboards/{ids['dash']}/data").json()
    assert set(data) == {w["id"] for w in widgets}
    for wid, res in data.items():
        assert isinstance(res, dict), f"{wid}: {res}"
        assert res["rows"], wid
    lib = api.get("/dashboards").json()
    assert [d["id"] for d in lib] == [ids["dash"]]


def _script(llm: FakeLLM, pattern: str, ops: list[dict[str, Any]], note: str) -> None:
    llm.rules.insert(0, FakeRule(respond=lambda r: tool_response(("patch_dashboard", {"ops": ops, "note": note})),
                                 task="converse", state="EDIT", pattern=pattern, times=1))  # fmt: skip


R8: list[tuple[str, Callable[[dict[str, Any]], list[dict[str, Any]]]]] = [
    ("widget data", lambda s: [
        {"op": "replace", "path": f"/widgets/{widget(s, lambda w: w['query'].get('top_n'))['id']}/query/top_n", "value": 5},
    ]),
    ("widget visuals", lambda s: [
        {"op": "replace", "path": f"/widgets/{widget(s, lambda w: w['type'] == 'kpi')['id']}/thresholds",
         "value": [{"below": 0.9, "color": "critical"}]},
        {"op": "add", "path": f"/widgets/{widget(s, lambda w: w['type'] == 'line')['id']}/options", "value": {"log_scale": True}},
    ]),
    ("widget content", lambda s: [
        {"op": "replace", "path": f"/widgets/{widget(s, lambda w: w['type'] == 'line')['id']}/title", "value": "Quality per build"},
        {"op": "add", "path": f"/widgets/{widget(s, lambda w: w['type'] == 'line')['id']}/annotations/-",
         "value": {"type": "rule", "y": 0.95, "label": "Target"}},
    ]),
    ("layout", lambda s: [
        {"op": "replace", "path": f"/layout/items/{widget(s, lambda w: w['type'] == 'line')['id']}/w", "value": 12},
        {"op": "replace", "path": f"/layout/items/{widget(s, lambda w: w['type'] == 'line')['id']}/y", "value": 40},
    ]),
    ("dashboard", lambda s: [
        {"op": "remove", "path": f"/widgets/{widget(s, lambda w: w['type'] == 'donut')['id']}"},
        {"op": "remove", "path": f"/layout/items/{widget(s, lambda w: w['type'] == 'donut')['id']}"},
        {"op": "replace", "path": "/title", "value": "Regression overview"},
        {"op": "add", "path": "/filters/f_suite", "value": {"id": "f_suite", "field": "suite", "control": "multiselect"}},
    ]),
    ("bulk", lambda s: [
        {"op": "replace", "path": "/measures/avg_duration/expr", "value": "percentile(duration_ms, 95)"},
        *({"op": "replace", "path": f"/widgets/{w['id']}/title", "value": w["title"].replace("Average", "p95")}
          for w in s["spec"]["widgets"] if "avg_duration" in w["query"].get("measures", [])),
    ]),
]  # fmt: skip


@pytest.mark.parametrize(("scope", "make_ops"), R8, ids=[r[0] for r in R8])
def test_edit_scope_then_undo_restores_the_body_byte_for_byte(
    api: TestClient,
    llm: FakeLLM,
    scope: str,
    make_ops: Callable[[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    ids = build_dashboard(api)
    before = spec(api, ids["dash"])
    ops = make_ops(before)
    _script(llm, r"please edit", ops, f"{scope} edit")
    reply = say(api, ids["sid"], f"please edit the {scope}")
    assert reply["state"] == "EDIT"
    assert "Apply" in reply["reply"]
    assert (
        spec(api, ids["dash"])["version"] == before["version"]
    )  # nothing saved until the user applies
    events = api.get(f"/sessions/{ids['sid']}/events", params={"follow": "false"}).text
    assert "edit_preview" in events

    applied = api.post(f"/sessions/{ids['sid']}/edit/apply")
    assert applied.status_code == 200, applied.text
    after = spec(api, ids["dash"])
    assert after["version"] == before["version"] + 1  # one version, even for a bulk edit
    assert after["spec"] != before["spec"]
    audit_note = api.get(f"/dashboards/{ids['dash']}/versions").json()[-1]["change_note"]
    assert audit_note == f"{scope} edit"
    assert all(
        isinstance(r, dict) for r in api.get(f"/dashboards/{ids['dash']}/data").json().values()
    )

    undo = say(api, ids["sid"], "undo that")
    assert "Restored version" in undo["reply"]
    restored = spec(api, ids["dash"])
    assert restored["version"] == before["version"] + 2
    assert json.dumps(restored["spec"], sort_keys=True) == json.dumps(
        before["spec"], sort_keys=True
    )


def test_chat_rename_and_change_type_with_the_offline_policy(api: TestClient) -> None:
    ids = build_dashboard(api)
    line = widget(spec(api, ids["dash"]), lambda w: w["type"] == "line")
    say(api, ids["sid"], "rename the pass rate by build to Build health")
    assert api.post(f"/sessions/{ids['sid']}/edit/apply").status_code == 200
    renamed = widget(spec(api, ids["dash"]), lambda w: w["id"] == line["id"])
    assert renamed["title"] == "Build health"
    say(api, ids["sid"], "make the build health a bar chart", selected_widget_ids=[line["id"]])
    assert api.post(f"/sessions/{ids['sid']}/edit/apply").status_code == 200
    assert widget(spec(api, ids["dash"]), lambda w: w["id"] == line["id"])["type"] == "bar"


def test_invalid_edits_are_refused_with_a_reason(api: TestClient, llm: FakeLLM) -> None:
    ids = build_dashboard(api)
    s = spec(api, ids["dash"])
    kpi = widget(s, lambda w: w["type"] == "kpi")
    resp = api.patch(f"/dashboards/{ids['dash']}", json={
        "base_version": s["version"], "ops": [{"op": "replace", "path": f"/widgets/{kpi['id']}/type", "value": "donut"}],
    })  # fmt: skip
    assert resp.status_code == 422
    assert "donut" in resp.json()["detail"]


def test_concurrent_patches_on_the_same_base(api: TestClient) -> None:
    ids = build_dashboard(api)
    s = spec(api, ids["dash"])

    def rename(title: str) -> int:
        return api.patch(f"/dashboards/{ids['dash']}", json={
            "base_version": s["version"], "ops": [{"op": "replace", "path": "/title", "value": title}],
        }).status_code  # fmt: skip

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(rename, ["A", "B"]))
    assert codes == [200, 409]
    assert rename("C") == 409  # still stale


def test_second_upload_refreshes_a_live_dashboard(api: TestClient) -> None:
    ids = build_dashboard(api, builds=4)
    trend = widget(
        spec(api, ids["dash"]),
        lambda w: w["type"] == "line" and w["query"]["dimensions"] == ["build"],
    )
    builds_before = [
        r[0] for r in api.get(f"/dashboards/{ids['dash']}/data").json()[trend["id"]]["rows"]
    ]
    assert "105" not in builds_before
    status = upload(api, FIXTURES / JUNIT[4], dataset_id=ids["dataset_id"])
    assert status["status"] == "done"
    result = api.post(f"/dashboards/{ids['dash']}/refresh", json={"upload_id": status["id"]}).json()
    assert result["status"] == "refreshed", result
    builds_after = [
        r[0] for r in api.get(f"/dashboards/{ids['dash']}/data").json()[trend["id"]]["rows"]
    ]
    assert builds_after == [*builds_before, "105"]


def test_incompatible_upload_gets_a_mapping_proposal(api: TestClient) -> None:
    ids = build_dashboard(api)
    status = upload(api, FIXTURES / "jmeter-load.jtl", dataset_id=ids["dataset_id"])
    assert status["status"] == "failed"
    result = api.post(f"/dashboards/{ids['dash']}/refresh", json={"upload_id": status["id"]}).json()
    assert result["status"] == "mapping_needed"
    assert result["mapping"]["timestamp"] == "timestamp"


def test_versions_diff_and_restore_endpoints(api: TestClient) -> None:
    ids = build_dashboard(api)
    s = spec(api, ids["dash"])
    api.patch(
        f"/dashboards/{ids['dash']}",
        json={"base_version": 1, "ops": [{"op": "replace", "path": "/title", "value": "X"}]},
    )
    d = api.get(f"/dashboards/{ids['dash']}/diff", params={"from": 1, "to": 2}).json()
    assert d == [{"op": "replace", "path": "/title", "value": "X"}]
    restored = api.post(f"/dashboards/{ids['dash']}/restore/1").json()
    assert restored["version"] == 3
    assert restored["spec"] == s["spec"]
    assert [v["version"] for v in api.get(f"/dashboards/{ids['dash']}/versions").json()] == [
        1,
        2,
        3,
    ]
    assert api.get("/dashboards", params={"q": "no such dashboard"}).json() == []
    assert (
        len(api.get("/dashboards", params={"q": "test exec", "domain": "test_execution"}).json())
        == 1
    )


def test_reopened_dashboard_session_edits_by_chat(api: TestClient) -> None:
    ids = build_dashboard(api)
    sid = api.post("/sessions", json={"dashboard_id": ids["dash"]}).json()
    assert sid["state"] == "PUBLISHED"
    reply = say(api, sid["id"], "rename the slowest tests to Slowest cases")
    assert reply["state"] == "EDIT"
    assert api.post(f"/sessions/{sid['id']}/edit/apply").status_code == 200
