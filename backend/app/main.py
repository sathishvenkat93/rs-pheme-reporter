"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import datasets, health, plans, query, sessions, uploads
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Pheme API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (
        health.router,
        sessions.router,
        uploads.router,
        datasets.router,
        query.router,
        plans.router,
    ):
        app.include_router(router)
    return app


app = create_app()
