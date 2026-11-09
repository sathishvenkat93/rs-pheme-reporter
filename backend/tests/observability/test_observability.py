"""M13: tracing across API → agent → tool → DuckDB with LLM cost, JSON logs, and limits."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text

from app.api.deps import get_llm
from app.core import telemetry
from app.core.config import get_settings
from app.core.logging import JsonFormatter
from app.main import create_app
from app.orchestrator.llm import FakeLLM, LLMResponse, Request, Usage, text_response, tool_response
from app.orchestrator.offline import offline_llm

from ..conftest import owner_engine
from ..ingestion.helpers import FIXTURES
from ..orchestrator.conftest import junit_session

EXPORTER = InMemorySpanExporter()
telemetry.add_processor(SimpleSpanProcessor(EXPORTER))


@pytest.fixture
def api(client: TestClient) -> TestClient:
    llm = offline_llm()
    client.app.dependency_overrides[get_llm] = lambda: llm  # type: ignore[attr-defined]
    return client


def test_one_request_is_traced_from_api_to_duckdb_with_llm_cost(api: TestClient) -> None:
    sid = junit_session(api)["id"]
    plan = api.get(f"/plans/{api.get(f'/sessions/{sid}').json()['plan_id']}").json()
    option = next(o for o in plan["options"] if o["chart_type"] == "bar")

    def first(r: Request) -> LLMResponse:
        resp = tool_response(("render_preview", {"option_id": option["id"]}))
        return resp.model_copy(
            update={
                "usage": Usage(input_tokens=1200, output_tokens=80),
                "model": "claude-sonnet-5-5",
            }
        )

    llm = FakeLLM()
    llm.on(first, task="converse", times=1)
    llm.on(lambda r: text_response("Here is the preview."), task="converse")
    api.app.dependency_overrides[get_llm] = lambda: llm  # type: ignore[attr-defined]

    EXPORTER.clear()
    assert (
        api.post(f"/sessions/{sid}/messages", json={"text": "preview that bar chart"}).status_code
        == 200
    )
    spans = EXPORTER.get_finished_spans()
    by_name = {s.name: s for s in spans}
    turn, tool, duck = (
        by_name["agent.turn"],
        by_name["tool.render_preview"],
        by_name["duckdb.query"],
    )
    http = next(
        s
        for s in spans
        if s.name.startswith("POST /sessions/{session_id}/messages") and s.parent is None
    )
    assert turn.parent is not None
    assert tool.parent is not None
    assert duck.parent is not None
    assert {turn.context.trace_id, tool.context.trace_id, duck.context.trace_id} == {
        http.context.trace_id
    }
    assert tool.parent.span_id == turn.context.span_id
    assert duck.parent.span_id == tool.context.span_id
    ancestors = {s.context.span_id: s for s in spans}
    node = turn
    while node.parent is not None and node.parent.span_id in ancestors:
        node = ancestors[node.parent.span_id]
    assert node is http  # agent.turn sits under the HTTP request span
    assert any(
        (s.attributes or {}).get("db.system") == "postgresql" for s in spans
    )  # SQL spans too

    llm_span = next(
        s
        for s in spans
        if s.name == "llm.converse" and (s.attributes or {}).get("llm.input_tokens") == 1200
    )
    attrs: dict[str, Any] = dict(llm_span.attributes or {})
    assert attrs["llm.model"] == "claude-sonnet-5-5"
    assert attrs["llm.output_tokens"] == 80
    assert attrs["llm.cost_usd"] == pytest.approx((1200 * 2.0 + 80 * 10.0) / 1e6)
    assert attrs["llm.latency_ms"] >= 0


def test_json_logs_carry_trace_ids() -> None:
    record = logging.LogRecord(
        "pheme.test", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    record.upload_id = "u-1"
    with telemetry.span("test.span"):
        line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "hello world"
    assert line["upload_id"] == "u-1"
    assert len(line["trace_id"]) == 32
    assert len(line["span_id"]) == 16


def test_rate_limit_returns_429_with_retry_after(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_per_min", 3)
    codes = [api.get("/dashboards").status_code for _ in range(3)]
    assert codes == [200, 200, 200]
    blocked = api.get("/dashboards")
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert api.get("/health").status_code == 200  # unauthenticated health isn't limited


def test_per_workspace_upload_limit(api: TestClient) -> None:
    api.get("/dashboards")  # provisions the dev workspace
    engine = owner_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE workspaces SET max_upload_bytes = 1000 WHERE id = :w"),
            {"w": str(get_settings().dev_workspace_id)},
        )
    engine.dispose()
    with (FIXTURES / "jmeter-load.jtl").open("rb") as fh:
        resp = api.post("/uploads", files={"file": ("jmeter-load.jtl", fh)})
    assert resp.status_code == 413
    with (FIXTURES / "malformed.json").open("rb") as fh:  # 20 bytes: under the workspace limit
        assert api.post("/uploads", files={"file": ("small.json", fh)}).status_code == 202


def test_llm_cost_table() -> None:
    assert telemetry.llm_cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert telemetry.llm_cost("someone-elses-model", 10, 10) is None


def test_exporters_follow_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "otel_exporter", "console")
    monkeypatch.setattr(get_settings(), "langfuse_host", "https://cloud.langfuse.example")
    monkeypatch.setattr(get_settings(), "langfuse_public_key", "pk")
    monkeypatch.setattr(get_settings(), "langfuse_secret_key", "sk")
    names = [type(e).__name__ for e in telemetry._exporters()]
    assert names == ["ConsoleSpanExporter", "OTLPSpanExporter"]


def test_app_still_starts_with_instrumentation() -> None:
    assert TestClient(create_app()).get("/health").status_code == 200
