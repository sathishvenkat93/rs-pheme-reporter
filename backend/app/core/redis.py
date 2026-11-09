"""Redis connection and job queue."""

from __future__ import annotations

from functools import lru_cache

from redis import Redis
from rq import Queue

from app.core.config import get_settings


@lru_cache
def get_redis() -> Redis:
    """Return the process-wide Redis client."""
    return Redis.from_url(get_settings().redis_url)


def get_queue() -> Queue:
    """Return the Pheme job queue."""
    return Queue(get_settings().queue_name, connection=get_redis())


def ping() -> None:
    """Raise if Redis is unreachable."""
    if not get_redis().ping():
        raise ConnectionError("redis did not answer PING")
