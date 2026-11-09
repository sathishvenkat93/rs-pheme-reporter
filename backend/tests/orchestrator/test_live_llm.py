"""One live run against the real models on Amazon Bedrock. Skipped unless PHEME_LIVE_LLM=1
(needs AWS credentials with Bedrock access; see PHEME_AWS_* in .env.example).

Records the transcript and token usage to docs/live-runs/<date>.md.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_llm
from app.orchestrator.llm import BedrockLLM, LLMResponse, Request, Usage

from .conftest import junit_session, say

pytestmark = pytest.mark.skipif(
    os.environ.get("PHEME_LIVE_LLM") != "1", reason="live LLM run (PHEME_LIVE_LLM=1)"
)
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}  # $ per 1M tokens in/out


class Recording(BedrockLLM):
    def __init__(self) -> None:
        super().__init__()
        self.log: list[tuple[Request, LLMResponse]] = []

    def complete(self, request: Request) -> LLMResponse:
        resp = super().complete(request)
        self.log.append((request, resp))
        return resp


def test_live_junit_session(base_client: TestClient) -> None:
    llm = Recording()
    base_client.app.dependency_overrides[get_llm] = lambda: llm  # type: ignore[attr-defined]
    s = junit_session(base_client)
    assert s["state"] == "PROPOSE"
    reply = say(base_client, s["id"], "Show a pie of failures by test, and drop the detail table.")
    assert reply["reply"]
    total = Usage()
    cost = 0.0
    lines = [f"# Live LLM run — {dt.date.today()}", "", "## Transcript", ""]
    for req, resp in llm.log:
        total = total + resp.usage
        p_in, p_out = PRICES.get(resp.model.removeprefix("anthropic.").split("-2")[0], (0, 0))
        cost += resp.usage.input_tokens / 1e6 * p_in + resp.usage.output_tokens / 1e6 * p_out
        lines += [f"### {req.task} ({resp.model}, {req.state})", "", "```json",
                  json.dumps(resp.content, indent=1)[:4000], "```", ""]  # fmt: skip
    lines += ["## Usage", "", f"- tokens: {total.model_dump()}", f"- estimated cost: ${cost:.4f}", "",
              f"Final reply: {reply['reply']}"]  # fmt: skip
    out = Path(__file__).resolve().parents[3] / "docs" / "live-runs" / f"{dt.date.today()}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
