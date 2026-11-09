"""Session events over a Redis Stream per session (SSE source; replayable with Last-Event-ID)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.redis import get_redis

MAXLEN = 5000  # events kept per session
EVENT_TYPES = frozenset({
    "assistant_delta", "assistant_message", "tool_progress", "options", "plan_diff",
    "approval_card", "approval_confirm", "state", "decision", "edit_preview", "dashboard", "error",
})  # fmt: skip


def _key(session_id: UUID) -> str:
    return f"pheme:events:{session_id}"


def publish(session_id: UUID, kind: str, data: Any) -> str:
    if kind not in EVENT_TYPES:
        raise ValueError(f"unknown event type {kind!r}")
    payload = {"type": kind, "data": json.dumps(data, default=str)}
    event_id: Any = get_redis().xadd(_key(session_id), payload, maxlen=MAXLEN, approximate=True)  # type: ignore[arg-type]
    return event_id.decode() if isinstance(event_id, bytes) else str(event_id)


def read(
    session_id: UUID, after: str = "0-0", *, block_ms: int | None = None
) -> list[tuple[str, str, Any]]:
    """Events after ``after`` as (id, type, data). ``block_ms`` waits for new ones."""
    res: Any = get_redis().xread({_key(session_id): after}, count=500, block=block_ms)
    out: list[tuple[str, str, Any]] = []
    for _, entries in res or []:
        for eid, fields in entries:
            f = {k.decode(): v.decode() for k, v in fields.items()}
            out.append((eid.decode(), f["type"], json.loads(f["data"])))
    return out


def sse(session_id: UUID, after: str, *, follow: bool, block_ms: int = 4000) -> Iterator[str]:
    """SSE frames. With ``follow`` it keeps waiting (heartbeats every ``block_ms``).

    ``after="$"`` starts from the newest event (only what happens from now on).
    """
    last = latest_id(session_id) if after == "$" else after
    while True:
        try:
            # block_ms stays under redis-py's default 5 s socket timeout
            batch = read(session_id, last, block_ms=block_ms if follow else None)
        except RedisTimeoutError:
            batch = []
        for eid, kind, data in batch:
            last = eid
            yield f"id: {eid}\nevent: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
        if not follow:
            return
        if not batch:
            yield ": keep-alive\n\n"


def latest_id(session_id: UUID) -> str:
    res: Any = get_redis().xrevrange(_key(session_id), count=1)
    return res[0][0].decode() if res else "0-0"
