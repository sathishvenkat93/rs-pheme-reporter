from __future__ import annotations

from rq import SimpleWorker

from app.core.redis import get_queue
from app.jobs.tasks import noop


def test_noop_job_runs_through_a_worker(redis_conn: object) -> None:
    queue = get_queue()
    job = queue.enqueue(noop, "hello")
    SimpleWorker([queue], connection=queue.connection).work(burst=True)
    assert job.get_status(refresh=True) == "finished"
    assert job.return_value() == "hello"
