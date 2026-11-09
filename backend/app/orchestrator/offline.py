"""Offline behaviour for FakeLLM: a heuristic intent parser and a scripted agent policy.

``PHEME_LLM=fake`` (the default) uses this, so the whole app (and the e2e tests) runs
without an API key. It only ever calls the same tools a real model would.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.orchestrator.llm import (
    FakeLLM,
    LLMResponse,
    Request,
    json_response,
    text_response,
    tool_response,
)

CHARTS = r"pie|donut|bar|column|line|area|histogram|scatter|table|heatmap|box(?: plot)?|pareto|stacked bar|kpi|gauge|treemap|sankey|globe|map|3d globe"
_DATA = re.compile(r'<data kind="(?P<kind>[^"]+)">\n(?P<body>.*?)\n</data>', re.DOTALL)
_USER = re.compile(r"<user_message>\n(?P<text>.*?)\n</user_message>", re.DOTALL)


def _blocks(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in _DATA.finditer(text):
        try:
            out[m["kind"]] = json.loads(m["body"])
        except json.JSONDecodeError:
            out[m["kind"]] = m["body"]
    return out


def _option_for(words: str, options: list[dict[str, Any]]) -> str | None:
    words = words.lower().strip()
    scored = []
    for o in options:
        title = o["title"].lower()
        tokens = [
            t
            for t in re.findall(r"[a-z0-9]+", words)
            if t not in {"the", "a", "an", "chart", "graph"}
        ]
        hits = sum(1 for t in tokens if t in title or t == o["chart"])
        if hits:
            scored.append((hits, o["status"] != "optional", o["id"]))
    return max(scored)[2] if scored else None


def parse_intents(request: Request) -> LLMResponse:
    data = _blocks(request.last_user_text)
    text = str(data.get("message", "")).strip()
    options = data.get("plan_options", []) or []
    low = text.lower()
    out: dict[str, Any] = {
        "requests": [],
        "ops": [],
        "approve": False,
        "undo": False,
        "explain": None,
    }
    if re.search(r"\b(approve|looks good|build it|ship it|go ahead)\b", low):
        out["approve"] = True
    if re.search(r"\b(undo|go back|revert)\b", low):
        out["undo"] = True
    if m := re.match(r"why (.+)", low):
        out["explain"] = m.group(1)
    drop = re.search(r"\b(?:drop|remove|delete)\s+(?:the\s+)?(.+)", low)
    if drop and (oid := _option_for(drop.group(1), options)):
        out["ops"].append({"op": "remove", "option_id": oid})
    if m := re.search(rf"\bmake\s+(?:the\s+)?(.+?)\s+an?\s+({CHARTS})(?:\s+chart)?\b", low):
        chart = {
            "column": "bar",
            "pie": "donut",
            "box plot": "box",
            "stacked bar": "stacked_bar",
        }.get(m.group(2), m.group(2))
        if oid := _option_for(m.group(1), options):
            out["ops"].append({"op": "change_type", "option_id": oid, "chart_type": chart})
    for m in re.finditer(
        rf"\b(?:(?P<grain>hourly|daily|weekly|minute)\s+)?(?P<chart>{CHARTS}|trend)(?:\s+chart)?\s+of\s+(?P<what>.+?)(?:\s+(?:by|per|over|vs|versus)\s+(?P<by>.+?))?(?:[.,;]|$)",
        low,
    ):
        chart = m["chart"]
        phrases = [{"text": m["what"].strip(), "role": "measure" if m["by"] else None}]
        if m["by"]:
            phrases.append(
                {"text": m["by"].strip(), "role": "split" if chart in ("pie", "donut") else "x"}
            )
        out["requests"].append({
            "raw_text": text[m.start():m.end()].strip(" .,;") or text,
            "chart_type": "line" if chart == "trend" else chart,
            "field_phrases": phrases,
            "time_grain": m["grain"],
        })  # fmt: skip
    if (
        not out["requests"]
        and not out["ops"]
        and (m := re.match(r"(?:show|add)\s+(?:me\s+)?(.+)", low))
    ):
        out["requests"].append(
            {
                "raw_text": text,
                "chart_type": None,
                "field_phrases": [{"text": m.group(1).strip(" .")}],
            }
        )
    return json_response(out)


def _tool_results(request: Request) -> list[tuple[str, Any]]:
    """(tool name, parsed output) for the tool results in the last user message."""
    last = request.messages[-1]
    if last["role"] != "user" or not isinstance(last["content"], list):
        return []
    names = {}
    for m in request.messages:
        if m["role"] == "assistant":
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    names[b["id"]] = b["name"]
    out = []
    for b in last["content"]:
        if b.get("type") == "tool_result":
            try:
                payload = json.loads(b["content"])
            except (json.JSONDecodeError, TypeError):
                payload = b["content"]
            out.append((names.get(b["tool_use_id"], "?"), payload))
    return out


_RENAME = re.compile(
    r"\brename\s+(?:the\s+)?(?P<what>.+?)\s+to\s+[\"']?(?P<to>[^\"']+?)[\"']?\s*$", re.I
)
_RETYPE = re.compile(
    rf"\b(?:make|change|turn)\s+(?:the\s+)?(?P<what>.+?)\s+(?:in)?to\s+an?\s+(?P<chart>{CHARTS})(?:\s+chart)?\b",
    re.I,
)
_RETYPE2 = re.compile(
    rf"\bmake\s+(?:the\s+)?(?P<what>.+?)\s+an?\s+(?P<chart>{CHARTS})(?:\s+chart)?\b", re.I
)
_CHART_NAME = {"column": "bar", "pie": "donut", "box plot": "box", "stacked bar": "stacked_bar"}


def edit(request: Request, text: str, results: list[tuple[str, Any]]) -> LLMResponse:
    """EDIT state: undo, rename and change-type, via resolve_widget then patch_dashboard."""
    if not results:
        if re.search(r"\b(undo|go back|revert)\b", text, re.I):
            return tool_response(("revert_dashboard", {}))
        m = _RENAME.search(text) or _RETYPE.search(text) or _RETYPE2.search(text)
        if m:
            return tool_response(("resolve_widget", {"reference": m["what"]}))
        return text_response(
            "What would you like to change? You can rename a widget or change its chart type."
        )
    name, payload = results[0]
    if name == "resolve_widget" and isinstance(payload, dict):
        if payload.get("ambiguous"):
            options = ", ".join(c["title"] for c in payload["candidates"])
            return text_response(f"Which one do you mean: {options}?")
        if not payload.get("widgets"):
            return text_response(
                "I couldn't find that widget. Select it on the dashboard and try again."
            )
        wid = payload["widgets"][0]["id"]
        if m := _RENAME.search(text):
            ops = [{"op": "replace", "path": f"/widgets/{wid}/title", "value": m["to"].strip()}]
            return tool_response(
                ("patch_dashboard", {"ops": ops, "note": f"Renamed to {m['to'].strip()}"})
            )
        if m := (_RETYPE.search(text) or _RETYPE2.search(text)):
            chart = _CHART_NAME.get(m["chart"].lower(), m["chart"].lower())
            ops = [{"op": "replace", "path": f"/widgets/{wid}/type", "value": chart}]
            return tool_response(("patch_dashboard", {"ops": ops, "note": f"Changed to {chart}"}))
    if name == "patch_dashboard":
        return text_response("Here's a preview of the change. Click Apply to save it.")
    if name == "revert_dashboard" and isinstance(payload, dict):
        return text_response(
            f"Restored version {payload['restored']} as version {payload['version']}."
        )
    return text_response(payload if isinstance(payload, str) else "Done.")


def converse(request: Request) -> LLMResponse:
    results = _tool_results(request)
    edit_tools = {"resolve_widget", "patch_dashboard", "revert_dashboard", "get_dashboard"}
    if request.state == "EDIT" or (results and results[0][0] in edit_tools):
        m = _USER.search(request.last_user_text)
        return edit(request, m["text"] if m else request.last_user_text, results)
    if not results:
        m = _USER.search(request.last_user_text)
        text = m["text"] if m else request.last_user_text
        if "parse_chart_request" in request.tool_names:
            return tool_response(("parse_chart_request", {"text": text}))
        if "build_dashboard" in request.tool_names and re.search(r"\bbuild\b", text, re.I):
            return tool_response(("build_dashboard", {}))
        return text_response("Noted.")
    name, payload = results[0]
    if name == "build_dashboard" and isinstance(payload, dict):
        return text_response(f"Your dashboard is ready: {payload['url']}")
    if name == "parse_chart_request" and isinstance(payload, dict):
        calls: list[tuple[str, dict[str, Any]]] = [
            ("check_feasibility", {"request": r}) for r in payload["requests"]
        ]
        if payload["ops"]:
            calls.append(("update_plan", {"ops": payload["ops"]}))
        if payload["approve"]:
            calls.append(("request_approval", {"approve": True}))
        if calls:
            return tool_response(*calls)
        return text_response(
            'I didn\'t find a change to make. Try e.g. "add a bar chart of failures by suite".'
        )
    lines = []
    for name, payload in results:
        if name == "check_feasibility" and isinstance(payload, dict):
            verdict = payload["verdict"].replace("_", " ")
            reason = f" {payload['reasons'][0]}" if payload.get("reasons") else ""
            lines.append(f"Request checked: {verdict}.{reason}")
        elif name == "update_plan" and isinstance(payload, dict):
            lines.append("; ".join(payload.get("changes", [])) + ".")
        elif isinstance(payload, str):
            lines.append(payload)
    return text_response(" ".join(lines)[:600] or "Done.")


def offline_llm() -> FakeLLM:
    llm = FakeLLM()
    llm.on(parse_intents, task="parse_intents")
    llm.on(converse, task="converse")
    return llm
