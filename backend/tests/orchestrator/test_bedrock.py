"""BedrockLLM against a mocked Bedrock endpoint: no network, no AWS credentials."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from anthropic import AnthropicBedrockMantle

from app.core.config import get_settings
from app.orchestrator.llm import BedrockLLM, LLMRefusal, Request, make_llm


def reply(**over: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "anthropic.claude-sonnet-5-5",
        "content": [{"type": "text", "text": '{"ok": true}'}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 8, "cache_read_input_tokens": 100},
    } | over


def mocked(answer: dict[str, Any]) -> tuple[BedrockLLM, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=answer)

    client = AnthropicBedrockMantle(
        aws_region="eu-west-1",
        api_key="test-bedrock-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    return BedrockLLM(client=client), seen


def request(**over: Any) -> Request:
    base: dict[str, Any] = {
        "role": "main",
        "task": "curate",
        "state": "PROPOSE",
        "system": "You plan dashboards.",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
    }
    return Request(**(base | over))


def test_requests_go_to_bedrock_with_prefixed_models_and_no_fallbacks() -> None:
    llm, seen = mocked(reply())
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    resp = llm.complete(request(output_schema=schema))

    (sent,) = seen
    assert sent.url.host == "bedrock-mantle.eu-west-1.api.aws"
    assert sent.url.path == "/anthropic/v1/messages"
    body = json.loads(sent.content)
    assert body["model"] == "anthropic.claude-sonnet-5-5"
    assert body["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": schema},
    }
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "fallbacks" not in body
    assert "tools" not in body
    assert "server-side-fallback" not in sent.headers.get("anthropic-beta", "")
    assert resp.text == '{"ok": true}'
    assert resp.usage.input_tokens == 120
    assert resp.usage.cache_read_input_tokens == 100
    assert resp.model == "anthropic.claude-sonnet-5-5"


def test_fast_role_uses_the_fast_model_and_passes_tools() -> None:
    llm, seen = mocked(reply(model="anthropic.claude-haiku-5-5"))
    tool = {"name": "parse_chart_request", "description": "d", "input_schema": {"type": "object"}}
    llm.complete(request(role="fast", tools=[tool]))
    body = json.loads(seen[0].content)
    assert body["model"] == "anthropic.claude-haiku-5-5"
    assert body["output_config"] == {"effort": "low"}
    assert body["tools"] == [tool]
    assert "tool_choice" not in body


def test_refusal_raises() -> None:
    llm, _ = mocked(reply(content=[], stop_reason="refusal"))
    with pytest.raises(LLMRefusal):
        llm.complete(request())


def test_first_party_model_ids_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "model_main", "claude-sonnet-5-5")
    with pytest.raises(ValueError, match=r"anthropic\.claude-sonnet-5-5"):
        BedrockLLM(client=object())


def test_settings_select_and_configure_the_bedrock_client(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK"):
        monkeypatch.delenv(var, raising=False)
    s = get_settings()
    monkeypatch.setattr(s, "llm", "bedrock")
    monkeypatch.setattr(s, "aws_region", "ap-southeast-2")
    monkeypatch.setattr(s, "bedrock_api_key", "test-bedrock-key")
    llm = make_llm()
    assert isinstance(llm, BedrockLLM)
    assert str(llm.client.base_url).startswith(
        "https://bedrock-mantle.ap-southeast-2.api.aws/anthropic"
    )


def test_cost_estimates_understand_bedrock_ids() -> None:
    from app.core.telemetry import llm_cost

    assert llm_cost("anthropic.claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
