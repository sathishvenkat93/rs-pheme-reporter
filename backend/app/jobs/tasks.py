"""RQ job functions."""

from __future__ import annotations


def noop(value: str = "pong") -> str:
    """A job that does nothing; proves the worker pipeline works."""
    return value
