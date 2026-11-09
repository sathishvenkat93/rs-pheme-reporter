"""Logging setup. TODO(M13): structured JSON logs and OpenTelemetry."""

from __future__ import annotations

import logging

from app.core.config import get_settings


def configure_logging() -> None:
    """Configure root logging once."""
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
