"""RQ worker entrypoint: ``python -m app.jobs.worker``."""

from __future__ import annotations

from rq import Worker

from app.core.logging import configure_logging
from app.core.redis import get_queue, get_redis


def main() -> None:
    configure_logging()
    Worker([get_queue()], connection=get_redis()).work()


if __name__ == "__main__":
    main()
