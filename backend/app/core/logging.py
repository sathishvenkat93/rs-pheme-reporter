"""Logging: structured JSON lines (with trace and span ids) or plain text for local reading."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from app.core.config import get_settings

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from app.core.telemetry import current_ids

        trace_id, span_id = current_ids()
        out: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if trace_id:
            out["trace_id"], out["span_id"] = trace_id, span_id
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging() -> None:
    """Configure root logging once."""
    s = get_settings()
    root = logging.getLogger()
    if getattr(root, "_pheme_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if s.log_format == "json" else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))  # fmt: skip
    root.addHandler(handler)  # alongside any existing handlers (pytest, uvicorn)
    root.setLevel(s.log_level)
    root._pheme_configured = True  # type: ignore[attr-defined]
