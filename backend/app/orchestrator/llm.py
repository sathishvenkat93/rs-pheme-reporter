"""LLM access: one protocol, the Anthropic implementation, a scripted fake and the tool loop.

Tests never call the real model: they use ``FakeLLM``. ``AnthropicLLM`` is used only when
``PHEME_LLM=anthropic`` (or ``PHEME_LIVE_LLM=1`` in the live test).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

log = logging.getLogger(__name__)

Role = Literal["main", "fast"]
Block = dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(**{k: getattr(self, k) + getattr(other, k) for k in Usage.model_fields})


class LLMResponse(BaseModel):
    content: list[Block]
    stop_reason: str
    usage: Usage = Usage()
    model: str = ""

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[Block]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class LLMError(RuntimeError):
    """The model couldn't produce a usable answer."""


class LLMRefusal(LLMError):
    """The model (and its fallback) declined the request."""


@dataclass(frozen=True, slots=True)
class Request:
    """Everything one call sends — what FakeLLM matches on and tests inspect."""

    role: Role
    task: str
    state: str
    system: str
    messages: list[Block]
    tools: list[Block]
    output_schema: dict[str, Any] | None = None
    max_tokens: int = 16000

    @property
    def tool_names(self) -> list[str]:
        return [t["name"] for t in self.tools]

    @property
    def last_user_text(self) -> str:
        for m in reversed(self.messages):
            if m["role"] != "user":
                continue
            content = m["content"]
            if isinstance(content, str):
                return content
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if texts:
                return "\n".join(texts)
        return ""

    def dump(self) -> str:
        return json.dumps(
            {"system": self.system, "messages": self.messages, "tools": self.tools},
            default=str,
            sort_keys=True,
        )


class LLM(Protocol):
    def complete(self, request: Request) -> LLMResponse: ...


# ----------------------------------------------------------------------------- Anthropic


class AnthropicLLM:
    """Thin client over the Anthropic Python SDK (Messages API)."""

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self) -> None:
        import anthropic

        s = get_settings()
        self._anthropic = anthropic
        # The SDK retries connection errors, 408/409/429 and 5xx with backoff.
        self.client = anthropic.Anthropic(
            api_key=s.anthropic_api_key or None, max_retries=3, timeout=60.0
        )
        self.models = {"main": s.model_main, "fast": s.model_fast}
        self.effort = {"main": "medium", "fast": "low"}
        self._fallbacks_ok = True

    def complete(self, request: Request) -> LLMResponse:
        model = self.models[request.role]
        output_config: dict[str, Any] = {"effort": self.effort[request.role]}
        if request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.output_schema}
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": request.messages,
            "output_config": output_config,
            "cache_control": {"type": "ephemeral"},  # frozen system + fixed tool order cache well
        }
        if request.tools:
            params["tools"] = request.tools  # tool_choice stays "auto": forced choice is a 400 here
        try:
            if request.role == "main" and self._fallbacks_ok:
                msg = self.client.beta.messages.create(
                    **params, betas=[self.FALLBACK_BETA], fallbacks="default"
                )
            else:
                msg = self.client.messages.create(**params)
        except self._anthropic.BadRequestError as exc:
            if request.role == "main" and self._fallbacks_ok and "fallback" in str(exc).lower():
                log.warning("server-side fallbacks rejected; continuing without them: %s", exc)
                self._fallbacks_ok = False
                return self.complete(request)
            raise
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise LLMRefusal(
                f"The model declined this request ({getattr(details, 'category', None)})."
            )
        u = msg.usage
        return LLMResponse(
            content=[b.to_dict() for b in msg.content],
            stop_reason=msg.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read_input_tokens=u.cache_read_input_tokens or 0,
                cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            ),
            model=msg.model,
        )


# ----------------------------------------------------------------------------- FakeLLM

Responder = Callable[[Request], LLMResponse]


@dataclass
class FakeRule:
    """Answers when task, state, the last user text (regex) and offered tools all match."""

    respond: Responder
    task: str | None = None
    state: str | None = None
    pattern: str | None = None
    requires_tool: str | None = None
    times: int | None = None  # None = unlimited
    used: int = 0

    def matches(self, r: Request) -> bool:
        if self.times is not None and self.used >= self.times:
            return False
        if self.task is not None and r.task != self.task:
            return False
        if self.state is not None and r.state != self.state:
            return False
        if self.requires_tool is not None and self.requires_tool not in r.tool_names:
            return False
        return not (
            self.pattern is not None
            and not re.search(self.pattern, r.last_user_text, re.IGNORECASE)
        )


@dataclass
class FakeLLM:
    """Scripted model for tests and offline runs. Records every request it receives."""

    rules: list[FakeRule] = field(default_factory=list)
    calls: list[Request] = field(default_factory=list)

    def on(self, respond: Responder, **match: Any) -> FakeLLM:
        self.rules.append(FakeRule(respond=respond, **match))
        return self

    def complete(self, request: Request) -> LLMResponse:
        self.calls.append(request)
        for rule in self.rules:
            if rule.matches(request):
                rule.used += 1
                return rule.respond(request)
        return text_response(default_reply(request))


def text_response(text: str) -> LLMResponse:
    return LLMResponse(content=[{"type": "text", "text": text}], stop_reason="end_turn")


def json_response(payload: Any) -> LLMResponse:
    return text_response(json.dumps(payload))


def tool_response(*calls: tuple[str, dict[str, Any]], text: str = "") -> LLMResponse:
    content: list[Block] = [{"type": "text", "text": text}] if text else []
    for i, (name, args) in enumerate(calls):
        content.append({"type": "tool_use", "id": f"toolu_fake_{name}_{i}_{abs(hash(json.dumps(args, sort_keys=True))) % 10**8}",
                        "name": name, "input": args})  # fmt: skip
    return LLMResponse(content=content, stop_reason="tool_use")


def default_reply(request: Request) -> str:
    """What the fake says when no rule matches: valid JSON for structured tasks, else a short reply."""
    if request.output_schema is not None:
        return json.dumps(_empty_for(request.output_schema))
    return "Done."


def _empty_for(schema: dict[str, Any]) -> Any:
    t = schema.get("type")
    if t == "object":
        return {
            k: _empty_for(v)
            for k, v in schema.get("properties", {}).items()
            if k in schema.get("required", [])
        }
    if t == "array":
        return []
    if t == "boolean":
        return False
    if t in ("integer", "number"):
        return 0
    return None if "null" in str(schema.get("anyOf", "")) else ""


def traced(llm: LLM, request: Request) -> LLMResponse:
    """Call the model inside an ``llm.<task>`` span with model, tokens, latency and cost."""
    import time

    from app.core.telemetry import llm_cost, span

    with span(f"llm.{request.task}", **{"llm.role": request.role, "llm.state": request.state}) as s:
        start = time.perf_counter()
        resp = llm.complete(request)
        u = resp.usage
        model = resp.model or request.role
        s.set_attribute("llm.model", model)
        s.set_attribute("llm.input_tokens", u.input_tokens)
        s.set_attribute("llm.output_tokens", u.output_tokens)
        s.set_attribute("llm.cache_read_tokens", u.cache_read_input_tokens)
        s.set_attribute("llm.latency_ms", round((time.perf_counter() - start) * 1000, 1))
        cost = llm_cost(model, u.input_tokens, u.output_tokens, u.cache_read_input_tokens)
        if cost is not None:
            s.set_attribute("llm.cost_usd", cost)
        return resp


def make_llm() -> LLM:
    """The app's LLM: FakeLLM unless a real one is explicitly configured."""
    s = get_settings()
    if s.llm == "anthropic" or s.live_llm:
        return AnthropicLLM()
    from app.orchestrator.offline import offline_llm

    return offline_llm()


# ----------------------------------------------------------------------------- tool loop


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    output: Any
    is_error: bool


@dataclass
class LoopResult:
    text: str
    new_messages: list[Block]  # appended to the history, in order (append-only)
    calls: list[ToolCall]
    usage: Usage
    iterations: int


ToolsFor = Callable[[], list[Block]]  # re-read every iteration: the state may change mid-loop
Execute = Callable[[str, dict[str, Any]], tuple[Any, bool]]  # (output, is_error)


def run_tool_loop(
    llm: LLM,
    *,
    task: str,
    state: Callable[[], str],
    system: str,
    history: Sequence[Block],
    tools_for: ToolsFor,
    execute: Execute,
    max_iterations: int = 8,
    on_call: Callable[[ToolCall], None] | None = None,
) -> LoopResult:
    """Call the main model, run its tool calls, feed results back; stop when it answers."""
    messages = list(history)
    new: list[Block] = []
    calls: list[ToolCall] = []
    usage = Usage()
    for i in range(1, max_iterations + 1):
        resp = traced(llm, Request("main", task, state(), system, messages, tools_for()))
        usage = usage + resp.usage
        assistant = {
            "role": "assistant",
            "content": resp.content,
        }  # unchanged: keeps thinking blocks valid
        messages.append(assistant)
        new.append(assistant)
        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason != "tool_use" or not resp.tool_uses:
            return LoopResult(resp.text, new, calls, usage, i)
        offered = {t["name"] for t in tools_for()}
        results: list[Block] = []
        for use in resp.tool_uses:
            if use["name"] not in offered:
                out, err = f"Tool {use['name']!r} isn't available in the {state()} state.", True
            else:
                out, err = execute(use["name"], dict(use.get("input") or {}))
            call = ToolCall(use["name"], dict(use.get("input") or {}), out, err)
            calls.append(call)
            if on_call:
                on_call(call)
            results.append({
                "type": "tool_result", "tool_use_id": use["id"],
                "content": out if isinstance(out, str) else json.dumps(out, default=str), "is_error": err,
            })  # fmt: skip
        user = {"role": "user", "content": results}  # all results in one message
        messages.append(user)
        new.append(user)
    return LoopResult(
        "I couldn't finish that in time. Could you try a simpler request?",
        new,
        calls,
        usage,
        max_iterations,
    )


def structured[T: BaseModel](
    llm: LLM, model: type[T], *, role: Role, task: str, state: str, system: str, prompt: str
) -> T | None:
    """One structured call, validated with Pydantic; retried once with the error; None if still invalid."""
    schema = closed_schema(model.model_json_schema())
    messages: list[Block] = [{"role": "user", "content": prompt}]
    for attempt in range(2):
        resp = traced(
            llm,
            Request(role, task, state, system, messages, [], output_schema=schema, max_tokens=4000),
        )
        try:
            return model.model_validate_json(resp.text or "{}")
        except ValidationError as exc:
            if attempt == 1:
                log.info("structured output for %s invalid after retry: %s", task, exc.errors()[:3])
                return None
            messages += [
                {"role": "assistant", "content": resp.content or [{"type": "text", "text": "{}"}]},
                {
                    "role": "user",
                    "content": f"That didn't validate: {exc.errors()[:5]}. Reply with corrected JSON only.",
                },
            ]
    return None


def closed_schema(schema: Any) -> Any:
    """Set ``additionalProperties: false`` on every object (structured outputs need closed objects)."""
    if isinstance(schema, dict):
        out = {k: closed_schema(v) for k, v in schema.items()}
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [closed_schema(v) for v in schema]
    return schema
