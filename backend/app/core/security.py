"""PII and secret redaction applied before anything reaches the LLM (design rule 1).

Values are replaced by reversible placeholders such as ``«EMAIL_1»``. The mapping stays
server-side (``sessions.redactions``); ``restore`` puts originals back into LLM output
before it is written to a plan or spec. The same value always gets the same placeholder
within a session, so the model can still reason about repeats.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Order matters: more specific patterns first (a JWT also looks like long base64 text).
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    (
        "API_KEY",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}"
            r"|gh[pousr]_[A-Za-z0-9]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})\b"
        ),
    ),
    (
        "SECRET",
        re.compile(
            r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|pwd)\s*[=:]\s*['\"]?([^\s'\",;]{6,})"
        ),
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        "IPV6",
        re.compile(
            r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b|\b(?:[0-9A-Fa-f]{1,4}:){1,7}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?\b"
        ),
    ),
    ("IPV4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("HEX_SECRET", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
)

PLACEHOLDER = re.compile(r"«([A-Z][A-Z0-9_]*_\d+)»")


@dataclass
class Redactor:
    """Holds one session's mapping. ``mapping`` is placeholder → original."""

    mapping: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._reverse = {v: k for k, v in self.mapping.items()}
        self._counts: dict[str, int] = {}
        for key in self.mapping:
            kind, _, n = key.rpartition("_")
            self._counts[kind] = max(self._counts.get(kind, 0), int(n))

    def _placeholder(self, kind: str, value: str) -> str:
        if value in self._reverse:
            return f"«{self._reverse[value]}»"
        self._counts[kind] = self._counts.get(kind, 0) + 1
        key = f"{kind}_{self._counts[kind]}"
        self.mapping[key] = value
        self._reverse[value] = key
        return f"«{key}»"

    def redact(self, text: str) -> str:
        for kind, rx in PATTERNS:

            def sub(m: re.Match[str], kind: str = kind) -> str:
                if kind == "SECRET":  # keep the "token=" part, hide only the value
                    start, end = m.span(1)
                    whole = m.group(0)
                    rel_s, rel_e = start - m.start(), end - m.start()
                    return whole[:rel_s] + self._placeholder(kind, m.group(1)) + whole[rel_e:]
                return self._placeholder(kind, m.group(0))

            text = rx.sub(sub, text)
        return text

    def redact_value(self, value: Any) -> Any:
        """Redact every string inside a JSON-like value (dicts, lists, scalars)."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, Mapping):
            return {k: self.redact_value(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.redact_value(v) for v in value]
        return value

    def restore(self, text: str) -> str:
        return PLACEHOLDER.sub(lambda m: self.mapping.get(m.group(1), m.group(0)), text)

    def restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, Mapping):
            return {k: self.restore_value(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.restore_value(v) for v in value]
        return value


def data_block(kind: str, payload: Any, redactor: Redactor) -> str:
    """Wrap redacted data for the prompt. Data is never instructions (design rule 7)."""
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str, sort_keys=True)
    body = redactor.redact(body).replace("</data>", "<\\/data>")
    return f'<data kind="{kind}">\n{body}\n</data>'
