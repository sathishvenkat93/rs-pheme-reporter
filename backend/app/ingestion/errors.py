"""Ingestion errors carry a message written for the user, never a stack trace."""

from __future__ import annotations

import re

_INTERNAL = re.compile(r"\b(polars|pyarrow|lxml|fastexcel|calamine)\b[\w.]*", re.IGNORECASE)


class IngestionError(Exception):
    """A file Pheme can't ingest. ``user_message`` is safe to show as-is."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def readable(prefix: str, exc: BaseException, limit: int = 220) -> IngestionError:
    """Wrap a library error into a short, user-facing message.

    Keeps the first line of the library's message (it usually names the row or field),
    drops library and exception-class names, and truncates.
    """
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    first = _INTERNAL.sub("", first)
    first = re.sub(r"\b[A-Z][A-Za-z]+(Error|Exception)\b:?\s*", "", first)
    first = re.sub(r"(?i)\b(\w+\s)?error:\s*", "", first).strip(" :")
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    return IngestionError(f"{prefix}: {first}." if first else f"{prefix}.")
