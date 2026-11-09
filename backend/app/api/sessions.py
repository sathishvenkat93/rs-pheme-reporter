"""Sessions (minimal until the M8 state machine) and free-text context."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import DB, Caller
from app.db.tables import SessionRow
from app.ingestion.context import ContextFacts, extract

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionOut(BaseModel):
    id: UUID
    state: str


class ContextIn(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)


class ContextOut(ContextFacts):
    id: str


def _own_session(db: DB, caller: Caller, session_id: UUID) -> SessionRow:
    row = db.get(SessionRow, session_id)
    if row is None or row.workspace_id != caller.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    return row


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionOut)
def create_session(db: DB, caller: Caller) -> SessionOut:
    # TODO(M8): state machine; optional dashboard_id starts the session in PUBLISHED.
    row = SessionRow(workspace_id=caller.workspace_id, user_id=caller.user_id, state="INGEST")
    db.add(row)
    db.commit()
    return SessionOut(id=row.id, state=row.state)


@router.post(
    "/{session_id}/context", status_code=status.HTTP_201_CREATED, response_model=ContextOut
)
def add_context(session_id: UUID, body: ContextIn, db: DB, caller: Caller) -> ContextOut:
    row = _own_session(db, caller, session_id)
    found = extract(body.text)
    entry = {
        "id": uuid4().hex[:12],
        "text": body.text,
        "facts": found.facts,
        "thresholds": [t.model_dump() for t in found.thresholds],
        "created_at": datetime.now(UTC).isoformat(),
    }
    row.context = [*row.context, entry]
    db.commit()
    return ContextOut(id=entry["id"], facts=found.facts, thresholds=found.thresholds)
