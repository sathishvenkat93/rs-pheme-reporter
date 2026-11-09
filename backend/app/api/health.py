"""Health endpoint: checks every runtime dependency."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core import db, redis, storage

router = APIRouter(tags=["health"])

CHECKS: dict[str, Callable[[], None]] = {
    "postgres": db.ping,
    "redis": redis.ping,
    "object_store": storage.ping,
}


class HealthReport(BaseModel):
    status: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthReport)
def health(response: Response) -> HealthReport:
    results: dict[str, str] = {}
    for name, check in CHECKS.items():
        try:
            check()
            results[name] = "ok"
        except Exception as exc:  # any failure means "not ok"; report its type only
            results[name] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in results.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthReport(status="ok" if ok else "degraded", checks=results)
