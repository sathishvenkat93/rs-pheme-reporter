from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST: dict[str, dict[str, Any]] = json.loads((FIXTURES / "manifest.json").read_text())
VALID = sorted(n for n, m in MANIFEST.items() if m["rows"] is not None)
MALFORMED = sorted(n for n, m in MANIFEST.items() if m["rows"] is None and m["format"] != "unknown")
JUNIT = sorted(n for n in VALID if n.startswith("junit-"))

_TECHNICAL = re.compile(
    r"Traceback|File \"|\b[A-Z][a-z]+[A-Za-z]*(Error|Exception)\b|polars|pyarrow|lxml"
)


def assert_readable(message: str | None) -> None:
    assert message, "expected an error message"
    assert not _TECHNICAL.search(message), f"message looks technical: {message!r}"
    assert len(message) < 400
