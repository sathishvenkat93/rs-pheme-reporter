"""SQLAlchemy engine and sessions, with row-level security context per transaction."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.core.config import get_settings

WORKSPACE = "workspace_id"  # Session.info key: the caller's workspace
BYPASS = "rls_bypass"  # Session.info key: trusted system work (ingestion worker, provisioning)


@lru_cache
def get_engine() -> Engine:
    """Process-wide engine (connects as the non-owner app role)."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _set_rls_context(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    """Every transaction carries its workspace (``SET LOCAL``), so RLS policies can filter.

    The context lives on ``session.info`` rather than a contextvar: FastAPI runs a request's
    dependencies and endpoint in different threadpool contexts, but they share the session.
    """
    ws = session.info.get(WORKSPACE)
    connection.execute(
        text(
            "SELECT set_config('app.workspace_id', :ws, true), set_config('app.bypass', :bypass, true)"
        ),
        {"ws": str(ws) if ws else "", "bypass": "on" if session.info.get(BYPASS) else "off"},
    )


def new_session(*, workspace_id: UUID | None = None, bypass: bool = False) -> Session:
    """Open a session (caller closes it). ``bypass`` is for trusted system work only."""
    session = _session_factory()()
    info: dict[str, Any] = session.info
    info[WORKSPACE] = workspace_id
    info[BYPASS] = bypass
    return session


def scope(session: Session, workspace_id: UUID | None, *, bypass: bool = False) -> None:
    """Change the RLS context; ends the current transaction so the next one picks it up."""
    if session.in_transaction():
        session.commit()
    session.info[WORKSPACE] = workspace_id
    session.info[BYPASS] = bypass


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is closed afterwards."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def ping() -> None:
    """Raise if the database is unreachable."""
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
