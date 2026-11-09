"""Dashboards: library, versions, data, diff, restore, patch and refresh."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.api.deps import DB, Caller
from app.dashboards import store
from app.db.tables import DashboardRow, SessionRow
from app.models.patch import WidgetPatchOp
from app.models.spec import DashboardSpec
from app.orchestrator import events
from app.orchestrator.agent import AgentError
from app.orchestrator.dashboards import refresh
from app.orchestrator.states import State
from app.query.executor import QueryResult

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


class DashboardSummary(BaseModel):
    id: UUID
    title: str
    domain: str | None
    tags: list[str]
    owner_id: UUID
    version: int
    updated_at: datetime


class VersionOut(BaseModel):
    dashboard_id: UUID
    version: int
    spec: DashboardSpec
    author_id: UUID
    change_note: str | None
    created_at: datetime


class VersionMeta(BaseModel):
    version: int
    author_id: UUID
    change_note: str | None
    created_at: datetime


class PatchIn(BaseModel):
    base_version: int = Field(ge=1)
    ops: list[WidgetPatchOp] = Field(min_length=1)
    note: str = Field(default="Edited", max_length=300)


class RefreshIn(BaseModel):
    upload_id: UUID


def _dash(db: DB, caller: Caller, dashboard_id: UUID) -> DashboardRow:
    try:
        return store.get(db, dashboard_id, caller.workspace_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dashboard not found.") from None


def _out(v: store.Version) -> VersionOut:
    return VersionOut(dashboard_id=v.dashboard_id, version=v.version, spec=v.spec, author_id=v.author_id,
                      change_note=v.change_note, created_at=v.created_at)  # fmt: skip


@router.get("", response_model=list[DashboardSummary])
def library(
    db: DB,
    caller: Caller,
    q: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
    owner: UUID | None = None,
) -> list[DashboardSummary]:
    stmt = select(DashboardRow).where(DashboardRow.workspace_id == caller.workspace_id)
    if q:
        stmt = stmt.where(
            or_(DashboardRow.title.ilike(f"%{q}%"), DashboardRow.domain.ilike(f"%{q}%"))
        )
    if domain:
        stmt = stmt.where(DashboardRow.domain == domain)
    if tag:
        stmt = stmt.where(DashboardRow.tags.contains([tag]))
    if owner:
        stmt = stmt.where(DashboardRow.owner_id == owner)
    rows = db.scalars(stmt.order_by(DashboardRow.updated_at.desc())).all()
    return [DashboardSummary(id=r.id, title=r.title, domain=r.domain, tags=r.tags, owner_id=r.owner_id,
                             version=r.current_version, updated_at=r.updated_at) for r in rows]  # fmt: skip


@router.get("/{dashboard_id}", response_model=VersionOut)
def latest(dashboard_id: UUID, db: DB, caller: Caller) -> VersionOut:
    return _out(store.version(db, _dash(db, caller, dashboard_id)))


@router.get("/{dashboard_id}/versions", response_model=list[VersionMeta])
def list_versions(dashboard_id: UUID, db: DB, caller: Caller) -> list[VersionMeta]:
    return [VersionMeta(version=v.version, author_id=v.author_id, change_note=v.change_note, created_at=v.created_at)
            for v in store.versions(db, _dash(db, caller, dashboard_id))]  # fmt: skip


@router.get("/{dashboard_id}/versions/{n}", response_model=VersionOut)
def get_version(dashboard_id: UUID, n: int, db: DB, caller: Caller) -> VersionOut:
    try:
        return _out(store.version(db, _dash(db, caller, dashboard_id), n))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.get("/{dashboard_id}/data", response_model=dict[str, QueryResult | str])
def data(
    dashboard_id: UUID, request: Request, db: DB, caller: Caller, version: int | None = None
) -> dict[str, Any]:
    """Rows per widget. Global filters: ``?filter.<field>=a,b``."""
    dash = _dash(db, caller, dashboard_id)
    filters = {
        k.removeprefix("filter."): v.split(",")
        for k, v in request.query_params.items()
        if k.startswith("filter.")
    }
    return store.widget_data(db, store.version(db, dash, version).spec, filters)


@router.get("/{dashboard_id}/diff")
def diff(
    dashboard_id: UUID,
    db: DB,
    caller: Caller,
    from_: Annotated[int, Query(alias="from", ge=1)],
    to: Annotated[int, Query(ge=1)],
) -> list[dict[str, Any]]:
    dash = _dash(db, caller, dashboard_id)
    try:
        a, b = store.version(db, dash, from_), store.version(db, dash, to)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return store.diff(
        a.spec.model_dump(mode="json", exclude_none=True),
        b.spec.model_dump(mode="json", exclude_none=True),
    )


@router.post("/{dashboard_id}/restore/{n}", response_model=VersionOut)
def restore(dashboard_id: UUID, n: int, db: DB, caller: Caller) -> VersionOut:
    _dash(db, caller, dashboard_id)
    try:
        return _out(store.restore(db, dashboard_id, caller.workspace_id, n, caller.user_id))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.patch("/{dashboard_id}", response_model=VersionOut)
def patch(dashboard_id: UUID, body: PatchIn, db: DB, caller: Caller) -> VersionOut:
    _dash(db, caller, dashboard_id)
    try:
        v = store.patch(db, dashboard_id, caller.workspace_id, base_version=body.base_version, ops=body.ops,
                        author=caller.user_id, note=body.note)  # fmt: skip
    except store.StaleVersion as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except store.EditError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return _out(v)


@router.post("/{dashboard_id}/refresh")
def refresh_dashboard(
    dashboard_id: UUID, body: RefreshIn, db: DB, caller: Caller
) -> dict[str, Any]:
    dash = _dash(db, caller, dashboard_id)
    session = db.get(SessionRow, dash.session_id) if dash.session_id else None
    in_session = session is not None and session.state == State.PUBLISHED.value
    if in_session:
        assert session is not None
        session.state = State.REFRESH.value
        events.publish(session.id, "state", {"state": State.REFRESH.value})
    try:
        result = refresh(db, dash, caller.workspace_id, body.upload_id, caller.user_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AgentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    finally:
        if in_session:
            assert session is not None
            session.state = State.PUBLISHED.value
            events.publish(session.id, "state", {"state": State.PUBLISHED.value})
            db.commit()
    return result
