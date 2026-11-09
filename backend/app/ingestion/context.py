"""Regex extraction of simple facts from free-text context. TODO(M8): LLM extraction."""

from __future__ import annotations

import re

from pydantic import BaseModel

_KV = re.compile(r"\b([A-Za-z_][\w.-]*)\s*=\s*([^\s,;=]+)")
_METRIC = (
    r"p\d{1,2}(?:\.\d)?|avg|average|mean|median|max|min|error[ _]rate|pass[ _]rate|"
    r"success[ _]rate|latency|response[ _]time|throughput|duration"
)
_THRESHOLD = re.compile(
    rf"\b(?P<metric>{_METRIC})\b(?:\s+\w+){{0,3}}?\s*"
    r"(?:must|should|needs to|has to)?\s*(?:be|stay|remain)?\s*"
    r"(?P<op><=|>=|<|>|=<|=>|under|below|less than|above|over|more than|at most|at least|within)\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|sec|seconds|%|rps|req/s|qps)?",
    re.IGNORECASE,
)
_OPS = {
    "<": "<", "under": "<", "below": "<", "less than": "<", "within": "<=",
    ">": ">", "above": ">", "over": ">", "more than": ">",
    "<=": "<=", "=<": "<=", "at most": "<=",
    ">=": ">=", "=>": ">=", "at least": ">=",
}  # fmt: skip
_UNITS = {"sec": "s", "seconds": "s", "req/s": "rps", "qps": "rps"}


class Threshold(BaseModel):
    metric: str
    op: str
    value: float
    unit: str | None = None


class ContextFacts(BaseModel):
    facts: dict[str, str]
    thresholds: list[Threshold]


def extract(text: str) -> ContextFacts:
    """Find ``key=value`` facts and threshold statements such as "p95 must be < 800 ms"."""
    facts = {k.lower(): v for k, v in _KV.findall(text)}
    thresholds = []
    for m in _THRESHOLD.finditer(text):
        metric = re.sub(r"[ ]", "_", m.group("metric").lower())
        metric = {"average": "avg", "mean": "avg", "response_time": "latency"}.get(metric, metric)
        unit = m.group("unit")
        unit = _UNITS.get(unit.lower(), unit.lower()) if unit else None
        thresholds.append(
            Threshold(
                metric=metric,
                op=_OPS[m.group("op").lower()],
                value=float(m.group("value")),
                unit=unit,
            )
        )
    return ContextFacts(facts=facts, thresholds=thresholds)
