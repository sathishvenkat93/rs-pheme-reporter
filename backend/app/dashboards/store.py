"""Dashboards and their append-only versions: create, patch, restore, diff and data."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import (
    AuditEvent,
    DashboardRow,
    DashboardVersionRow,
    DatasetRow,
    DatasetVersionRow,
)
from app.models.chart import OptionStatus, VisualOption
from app.models.dataset import ColumnSchema, DatasetProfile
from app.models.patch import PatchError, RfcOp, WidgetPatchOp, apply_ops
from app.models.query import InFilter, Measure
from app.models.spec import DashboardSpec, Widget
from app.profiling.service import latest_version, profile_version
from app.query.compiler import QueryError
from app.query.executor import QueryResult, Target, execute
from app.recommend.context import context_for, violations


class StaleVersion(Exception):
    """The patch was based on an older version (HTTP 409)."""

    def __init__(self, current: int) -> None:
        super().__init__(f"The dashboard changed (now version {current}). Reload and try again.")
        self.current = current


class EditError(ValueError):
    """An edit that would produce an invalid dashboard. The message is meant for users."""


@dataclass(frozen=True)
class Version:
    dashboard_id: UUID
    version: int
    spec: DashboardSpec
    author_id: UUID
    change_note: str | None
    created_at: Any


def _version(row: DashboardVersionRow) -> Version:
    return Version(row.dashboard_id, row.version, DashboardSpec.model_validate(row.spec), row.author_id,
                   row.change_note, row.created_at)  # fmt: skip


def get(db: Session, dashboard_id: UUID, workspace_id: UUID) -> DashboardRow:
    row = db.get(DashboardRow, dashboard_id)
    if row is None or row.workspace_id != workspace_id:
        raise LookupError("Dashboard not found.")
    return row


def version(db: Session, dash: DashboardRow, n: int | None = None) -> Version:
    stmt = select(DashboardVersionRow).where(DashboardVersionRow.dashboard_id == dash.id)
    stmt = stmt.where(DashboardVersionRow.version == (n or dash.current_version))
    row = db.scalars(stmt).first()
    if row is None:
        raise LookupError(f"Version {n} not found.")
    return _version(row)


def versions(db: Session, dash: DashboardRow) -> list[Version]:
    rows = db.scalars(
        select(DashboardVersionRow)
        .where(DashboardVersionRow.dashboard_id == dash.id)
        .order_by(DashboardVersionRow.version)
    ).all()
    return [_version(r) for r in rows]


# ----------------------------------------------------------------------------- data


def _target(db: Session, spec: DashboardSpec) -> tuple[Target, DatasetRow]:
    binding = spec.datasets[0]
    dataset = db.get(DatasetRow, binding.dataset_id)
    if dataset is None:
        raise LookupError("The dashboard's dataset is gone.")
    ver: DatasetVersionRow | None
    if binding.mode == "snapshot" and binding.version_id is not None:
        ver = db.get(DatasetVersionRow, binding.version_id)
    else:
        ver = latest_version(db, dataset.id)
    assert ver is not None
    return Target(ver.id, ver.parts, [ColumnSchema.model_validate(c) for c in ver.columns]), dataset


def widget_measures(spec: DashboardSpec, w: Widget) -> list[Measure]:
    defs = {m.id: m for m in spec.measures}
    return [defs[m] for m in w.query.measures]


def widget_data(
    db: Session,
    spec: DashboardSpec,
    filters: dict[str, list[str]] | None = None,
    *,
    only: set[str] | None = None,
) -> dict[str, QueryResult | str]:
    """Rows per widget (honouring global filters); an error message instead of rows if one fails."""
    target, _ = _target(db, spec)
    columns = {c.name for c in target.columns}
    extra = [
        InFilter(field=f, values=list(vals))
        for f, vals in (filters or {}).items()
        if f in columns and vals
    ]
    out: dict[str, QueryResult | str] = {}
    for w in spec.widgets:
        if only is not None and w.id not in only:
            continue
        q = w.query.model_copy(update={"filters": [*w.query.filters, *extra]})
        try:
            out[w.id] = execute(target, q, widget_measures(spec, w))
        except QueryError as exc:
            out[w.id] = str(exc)
    return out


# ----------------------------------------------------------------------------- validation


def profile_for(db: Session, spec: DashboardSpec) -> DatasetProfile:
    target, _ = _target(db, spec)
    ver = db.get(DatasetVersionRow, target.version_id)
    assert ver is not None
    return profile_version(db, ver)


def validate(db: Session, doc: dict[str, Any]) -> DashboardSpec:
    """Schema, chart guard rules, then a dry-run of every widget query (limit 1)."""
    try:
        spec = DashboardSpec.model_validate(doc)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise EditError(f"That change makes the dashboard invalid: {first['msg']}.") from None
    ctx = context_for(profile_for(db, spec))
    target, _ = _target(db, spec)
    for w in spec.widgets:
        option = VisualOption(id=w.id, title=w.title, chart_type=w.type, query=w.query,
                              measures=widget_measures(spec, w), status=OptionStatus.OPTIONAL)  # fmt: skip
        if problems := violations(option, ctx):
            raise EditError(f"{w.title}: {'; '.join(problems)}.")
        try:
            execute(
                target, w.query.model_copy(update={"limit": 1}), option.measures, use_cache=False
            )
        except QueryError as exc:
            raise EditError(f"{w.title}: {exc}") from None
    return spec


# ----------------------------------------------------------------------------- writes


def _body(spec: DashboardSpec) -> dict[str, Any]:
    out: dict[str, Any] = spec.model_dump(mode="json", exclude_none=True)
    return out


def _audit(
    db: Session, dash: DashboardRow, actor: UUID, action: str, payload: dict[str, Any]
) -> None:
    db.add(AuditEvent(workspace_id=dash.workspace_id, actor_id=actor, action=action, target_type="dashboard",
                      target_id=str(dash.id), payload=payload))  # fmt: skip


def create(
    db: Session, *, workspace_id: UUID, owner_id: UUID, spec: DashboardSpec, session_id: UUID | None,
    plan_version_id: UUID | None, domain: str | None, dashboard_id: UUID,
) -> DashboardRow:  # fmt: skip
    dash = DashboardRow(id=dashboard_id, workspace_id=workspace_id, owner_id=owner_id, session_id=session_id,
                        title=spec.title, domain=domain, tags=[domain] if domain else [], current_version=1)  # fmt: skip
    db.add(dash)
    db.flush()
    db.add(DashboardVersionRow(workspace_id=workspace_id, dashboard_id=dash.id, version=1, spec=_body(spec),
                               plan_version_id=plan_version_id, author_id=owner_id, change_note="Built from the approved plan"))  # fmt: skip
    _audit(
        db,
        dash,
        owner_id,
        "dashboard.build",
        {"plan_version_id": str(plan_version_id) if plan_version_id else None},
    )
    db.flush()
    return dash


def _append(
    db: Session, dash: DashboardRow, spec: DashboardSpec, author: UUID, note: str
) -> Version:
    nxt = dash.current_version + 1
    db.add(DashboardVersionRow(workspace_id=dash.workspace_id, dashboard_id=dash.id, version=nxt, spec=_body(spec),
                               author_id=author, change_note=note[:500]))  # fmt: skip
    dash.current_version = nxt
    dash.title = spec.title
    try:
        db.flush()
    except IntegrityError:  # another writer took this version number first
        db.rollback()
        raise StaleVersion(nxt) from None
    return version(db, dash, nxt)


def lock(db: Session, dash_id: UUID, workspace_id: UUID) -> DashboardRow:
    row = db.scalars(
        select(DashboardRow).where(DashboardRow.id == dash_id).with_for_update()
    ).first()
    if row is None or row.workspace_id != workspace_id:
        raise LookupError("Dashboard not found.")
    return row


def plan_patch(
    db: Session, dash: DashboardRow, base_version: int, ops: Sequence[WidgetPatchOp]
) -> tuple[DashboardSpec, list[RfcOp]]:
    """Apply ``ops`` to ``base_version`` and validate, without saving (used for previews)."""
    if base_version != dash.current_version:
        raise StaleVersion(dash.current_version)
    base = version(db, dash, base_version)
    try:
        result = apply_ops(_body(base.spec), list(ops))
    except PatchError as exc:
        raise EditError(str(exc)) from None
    return validate(db, result.doc), result.forward


def patch(
    db: Session, dash_id: UUID, workspace_id: UUID, *, base_version: int, ops: Sequence[WidgetPatchOp],
    author: UUID, note: str, prompt: str | None = None,
) -> Version:  # fmt: skip
    dash = lock(db, dash_id, workspace_id)
    spec, forward = plan_patch(db, dash, base_version, ops)
    v = _append(db, dash, spec, author, note)
    _audit(db, dash, author, "dashboard.patch", {
        "version": v.version, "ops": [o.model_dump(mode="json") for o in ops],
        "rfc6902": [f.to_json() for f in forward], "prompt": prompt,
    })  # fmt: skip
    db.commit()
    return v


def restore(db: Session, dash_id: UUID, workspace_id: UUID, n: int, author: UUID) -> Version:
    dash = lock(db, dash_id, workspace_id)
    old = version(db, dash, n)
    v = _append(db, dash, validate(db, _body(old.spec)), author, f"Restored version {n}")
    _audit(db, dash, author, "dashboard.restore", {"from": n, "version": v.version})
    db.commit()
    return v


def save_spec(
    db: Session, dash_id: UUID, workspace_id: UUID, spec: DashboardSpec, author: UUID, note: str
) -> Version:
    dash = lock(db, dash_id, workspace_id)
    v = _append(db, dash, spec, author, note)
    _audit(db, dash, author, "dashboard.update", {"version": v.version, "note": note})
    db.commit()
    return v


def diff(a: Any, b: Any, path: str = "") -> list[dict[str, Any]]:
    """RFC 6902 ops that turn ``a`` into ``b`` (lists compared by position)."""
    if type(a) is not type(b):
        return [{"op": "replace", "path": path or "/", "value": b}]
    if isinstance(a, dict):
        out: list[dict[str, Any]] = []
        for k in sorted(set(a) | set(b)):
            p = f"{path}/{str(k).replace('~', '~0').replace('/', '~1')}"
            if k not in b:
                out.append({"op": "remove", "path": p})
            elif k not in a:
                out.append({"op": "add", "path": p, "value": b[k]})
            else:
                out += diff(a[k], b[k], p)
        return out
    if isinstance(a, list):
        out = []
        for i in range(min(len(a), len(b))):
            out += diff(a[i], b[i], f"{path}/{i}")
        out += [{"op": "add", "path": f"{path}/{i}", "value": b[i]} for i in range(len(a), len(b))]
        out += [{"op": "remove", "path": f"{path}/{i}"} for i in reversed(range(len(b), len(a)))]
        return out
    return [] if a == b else [{"op": "replace", "path": path or "/", "value": b}]


def body_bytes(spec: DashboardSpec) -> bytes:
    return json.dumps(_body(spec), sort_keys=True, separators=(",", ":")).encode()
