"""Sessions: create, chat, events (SSE), approve, and free-text context."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, Caller, Model
from app.core.security import Redactor
from app.dashboards.store import EditError, StaleVersion
from app.db.tables import Message
from app.ingestion.context import ContextFacts, extract
from app.orchestrator import events, plans
from app.orchestrator.agent import Agent, AgentError
from app.orchestrator.dashboards import apply_edit, discard_edit
from app.orchestrator.tasks import extract_context

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionIn(BaseModel):
    dashboard_id: UUID | None = None


class SessionOut(BaseModel):
    id: UUID
    state: str
    plan_id: UUID | None = None
    dashboard_id: UUID | None = None
    dataset_ids: list[UUID] = Field(default_factory=list)
    pending_edit: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    text: str
    created_at: datetime


class ToggleIn(BaseModel):
    included: bool


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    selected_widget_ids: list[str] = Field(default_factory=list)


class MessageOut(BaseModel):
    reply: str
    state: str


class ContextIn(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)


class ContextOut(ContextFacts):
    id: str


class ApproveOut(BaseModel):
    state: str
    plan_version: int


def _agent(db: DB, caller: Caller, llm: Model, session_id: UUID) -> Agent:
    try:
        return Agent.load(db, caller, llm, session_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.") from None


def _out(a: Agent) -> SessionOut:
    row = a.plan_row()
    return SessionOut(
        id=a.session.id,
        state=a.state.value,
        plan_id=row.id if row else None,
        dashboard_id=a.session.dashboard_id,
        dataset_ids=[d.id for d, _ in a.datasets()],
        pending_edit=a.session.pending_edit,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionOut)
def create_session(db: DB, caller: Caller, llm: Model, body: SessionIn | None = None) -> SessionOut:
    try:
        a = Agent.start(db, caller, llm, dashboard_id=body.dashboard_id if body else None)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dashboard not found.") from None
    return _out(a)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: UUID, db: DB, caller: Caller, llm: Model) -> SessionOut:
    a = _agent(db, caller, llm, session_id)
    if a.propose() is not None:  # uploads finished since the last call: build the proposal now
        a.commit()
    return _out(a)


@router.post("/{session_id}/messages", response_model=MessageOut)
def post_message(
    session_id: UUID, body: MessageIn, db: DB, caller: Caller, llm: Model
) -> MessageOut:
    a = _agent(db, caller, llm, session_id)
    try:
        reply = a.handle_message(body.text, body.selected_widget_ids)
    except AgentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return MessageOut(reply=reply, state=a.state.value)


@router.get("/{session_id}/messages", response_model=list[ChatMessage])
def list_messages(session_id: UUID, db: DB, caller: Caller, llm: Model) -> list[ChatMessage]:
    """The visible conversation (user and assistant turns), oldest first."""
    a = _agent(db, caller, llm, session_id)
    rows = db.scalars(
        select(Message)
        .where(Message.session_id == a.session.id, Message.role.in_(["user", "assistant"]))
        .order_by(Message.seq)
    ).all()
    return [
        ChatMessage(role=r.role, text=str(r.content.get("text", "")), created_at=r.created_at)
        for r in rows
    ]


@router.post("/{session_id}/options/{option_id}", response_model=SessionOut)
def toggle_option(
    session_id: UUID, option_id: str, body: ToggleIn, db: DB, caller: Caller, llm: Model
) -> SessionOut:
    """Include or exclude one proposed chart (the card toggle)."""
    a = _agent(db, caller, llm, session_id)
    try:
        a.toggle_option(option_id, body.included)
    except AgentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return _out(a)


@router.get("/{session_id}/events")
def stream_events(
    session_id: UUID,
    db: DB,
    caller: Caller,
    llm: Model,
    follow: Annotated[bool, Query()] = True,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[str | None, Query(pattern=r"^(\d+-\d+|\$)$")] = None,
) -> StreamingResponse:
    """SSE: assistant text, tool progress, options, plan diffs, approval cards, state changes.

    ``Last-Event-ID`` (or ``after``) replays what was missed; ``follow=false`` drains and returns.
    """
    _agent(db, caller, llm, session_id)
    start = last_event_id or after or "0-0"
    return StreamingResponse(
        events.sse(session_id, start, follow=follow), media_type="text/event-stream"
    )


@router.post("/{session_id}/approve", response_model=ApproveOut)
def approve(session_id: UUID, db: DB, caller: Caller, llm: Model) -> ApproveOut:
    """The only way into APPROVED: called by the user's own click."""
    a = _agent(db, caller, llm, session_id)
    try:
        plan = a.approve()
    except AgentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return ApproveOut(state=a.state.value, plan_version=plan.version)


class EditOut(BaseModel):
    dashboard_id: UUID
    version: int


@router.post("/{session_id}/edit/apply", response_model=EditOut)
def apply_pending_edit(session_id: UUID, db: DB, caller: Caller, llm: Model) -> EditOut:
    """Commit the agent's previewed edit (the user's Apply click)."""
    a = _agent(db, caller, llm, session_id)
    try:
        v = apply_edit(a)
    except (AgentError, StaleVersion, EditError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return EditOut(dashboard_id=v.dashboard_id, version=v.version)


@router.post("/{session_id}/edit/discard", status_code=status.HTTP_204_NO_CONTENT)
def discard_pending_edit(session_id: UUID, db: DB, caller: Caller, llm: Model) -> None:
    discard_edit(_agent(db, caller, llm, session_id))


@router.post(
    "/{session_id}/context", status_code=status.HTTP_201_CREATED, response_model=ContextOut
)
def add_context(
    session_id: UUID, body: ContextIn, db: DB, caller: Caller, llm: Model
) -> ContextOut:
    a = _agent(db, caller, llm, session_id)
    found = extract(body.text)
    redactor = Redactor(dict(a.session.redactions or {}))
    llm_found = extract_context(llm, body.text, redactor)
    facts = dict(found.facts)
    thresholds = [t.model_dump() for t in found.thresholds]
    if llm_found is not None:
        facts |= {k: v for k, v in llm_found.facts.items() if k not in facts}
        known = {(t["metric"], t["op"], t["value"]) for t in thresholds}
        thresholds += [
            t.model_dump() for t in llm_found.thresholds if (t.metric, t.op, t.value) not in known
        ]
    entry = {"id": uuid4().hex[:12], "text": body.text, "facts": facts, "thresholds": thresholds,
             "created_at": datetime.now(UTC).isoformat()}  # fmt: skip
    a.session.context = [*(a.session.context or []), entry]
    a.session.redactions = dict(redactor.mapping)
    db.commit()
    return ContextOut(id=entry["id"], facts=facts, thresholds=thresholds)


__all__ = ["plans", "router"]
