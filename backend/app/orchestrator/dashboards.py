"""Agent side of dashboards: build (APPROVED), the edit planner (EDIT), revert and refresh."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process
from sqlalchemy import or_, select

from app.dashboards import store
from app.dashboards.builder import BuildError, build_spec
from app.db.tables import DashboardRow, DatasetRow, PlanVersion, Upload
from app.ingestion import storage
from app.ingestion.detect import HEAD_BYTES, detect
from app.ingestion.normalize import ParseContext, columns_of
from app.ingestion.parsers import parse
from app.models.dataset import ColumnSchema, is_superset, schema_hash
from app.models.patch import WidgetPatchOp
from app.models.spec import DashboardSpec, Widget
from app.orchestrator import events, plans
from app.orchestrator.agent import Agent, AgentError, tool
from app.orchestrator.states import State
from app.profiling.service import latest_version

POSITION_WORDS = {"first": 0, "top": 0, "second": 1, "third": 2, "last": -1, "bottom": -1}


# ----------------------------------------------------------------------------- build


def build(a: Agent) -> DashboardRow:
    if a.state != State.APPROVED:
        raise AgentError("The plan has to be approved before a dashboard is built.")
    row = a.plan_row()
    assert row is not None
    assert row.approved_version is not None
    plan = plans.load(a.db, row, row.approved_version)
    (dataset, version), *_ = a.datasets()
    profiles = a.profiles()
    a.transition(State.BUILD)
    dash_id = uuid4()
    try:
        spec = build_spec(plan, dataset, version, dashboard_id=str(dash_id), domain=profiles[0].domain,
                          targets=a._targets_from_context())  # fmt: skip
        spec = store.validate(a.db, spec.model_dump(mode="json", exclude_none=True))
    except (BuildError, store.EditError) as exc:
        a.transition(State.REFINE)
        a.commit()
        raise AgentError(str(exc)) from None
    pv = a.db.scalars(
        select(PlanVersion).where(
            PlanVersion.plan_id == row.id, PlanVersion.version == plan.version
        )
    ).one()
    dash = store.create(a.db, workspace_id=a.principal.workspace_id, owner_id=a.principal.user_id, spec=spec,
                        session_id=a.session.id, plan_version_id=pv.id, domain=profiles[0].domain, dashboard_id=dash_id)  # fmt: skip
    store.widget_data(a.db, spec)  # run and cache every query now, so the first open is fast
    a.session.dashboard_id = dash.id
    a.transition(State.PUBLISHED)
    events.publish(
        a.session.id,
        "dashboard",
        {"dashboard_id": str(dash.id), "version": 1, "url": f"/d/{dash.id}"},
    )
    a.commit()
    return dash


# ----------------------------------------------------------------------------- edits


def current(a: Agent) -> tuple[DashboardRow, store.Version]:
    if a.session.dashboard_id is None:
        raise AgentError("This session has no dashboard yet.")
    dash = store.get(a.db, a.session.dashboard_id, a.principal.workspace_id)
    return dash, store.version(a.db, dash)


def resolve_widgets(
    spec: DashboardSpec, reference: str | None, selected: list[str]
) -> list[Widget]:
    """Selected ids win; otherwise match words against title, type, fields and position."""
    by_id = {w.id: w for w in spec.widgets}
    if selected:
        return [by_id[i] for i in selected if i in by_id]
    if not reference:
        return []
    ref = reference.lower()
    ordered = sorted(
        spec.widgets,
        key=lambda w: next(((i.y, i.x) for i in spec.layout.items if i.widget == w.id), (0, 0)),
    )
    for word, idx in POSITION_WORDS.items():
        if re.search(rf"\b{word}\b", ref) and ordered:
            return [ordered[idx]]
    tokens = [
        t
        for t in re.findall(r"[a-z0-9]+", ref)
        if t not in {"the", "a", "an", "chart", "widget", "graph"}
    ]
    scored: list[tuple[float, Widget]] = []
    for w in spec.widgets:
        text = " ".join([w.title, w.type.value, *w.query.dimensions, *w.query.measures]).lower()
        hits = sum(1 for t in tokens if t in text)
        fuzzy = fuzz.partial_ratio(ref, w.title.lower()) / 100
        if hits or fuzzy >= 0.8:
            scored.append((hits + fuzzy, w))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [w for s, w in scored if s >= best - 0.05]


def propose_edit(a: Agent, ops: list[WidgetPatchOp], note: str) -> dict[str, Any]:
    dash, cur = current(a)
    try:
        spec, _ = store.plan_patch(a.db, dash, cur.version, ops)
    except store.EditError as exc:
        raise AgentError(str(exc)) from None
    touched = {
        p.split("/")[2]
        for p in (o.path for o in ops)
        if p.startswith(("/widgets/", "/layout/items/"))
    }
    before = store.widget_data(a.db, cur.spec, only=touched)
    after = store.widget_data(a.db, spec, only=touched)

    def rows(res: Any) -> Any:
        return res.rows[:10] if hasattr(res, "rows") else res

    preview = {
        "base_version": cur.version, "note": note,
        "widgets": [{"id": w, "before": rows(before.get(w)), "after": rows(after.get(w))} for w in sorted(touched)],
        "changes": [o.model_dump(mode="json") for o in ops],
    }  # fmt: skip
    a.session.pending_edit = {"base_version": cur.version, "ops": preview["changes"], "note": note,
                              "prompt": a.last_user_text}  # fmt: skip
    events.publish(a.session.id, "edit_preview", preview)
    a.commit()
    return {"preview": "ready", "widgets": sorted(touched),
            "message": "The user sees before/after previews; the edit is saved only when they click Apply."}  # fmt: skip


def apply_edit(a: Agent) -> store.Version:
    pending = a.session.pending_edit
    if not pending:
        raise AgentError("There's no edit waiting to be applied.")
    dash, _ = current(a)
    ops = [WidgetPatchOp.model_validate(o) for o in pending["ops"]]
    try:
        v = store.patch(a.db, dash.id, a.principal.workspace_id, base_version=pending["base_version"], ops=ops,
                        author=a.principal.user_id, note=pending["note"], prompt=pending.get("prompt"))  # fmt: skip
    finally:
        a.session.pending_edit = None
        if a.state == State.EDIT:
            a.transition(State.PUBLISHED)
        a.commit()
    events.publish(a.session.id, "dashboard", {"dashboard_id": str(dash.id), "version": v.version})
    return v


def discard_edit(a: Agent) -> None:
    a.session.pending_edit = None
    if a.state == State.EDIT:
        a.transition(State.PUBLISHED)
    a.commit()


# ----------------------------------------------------------------------------- refresh


def proposed_mapping(new: list[ColumnSchema], old: list[ColumnSchema]) -> dict[str, str | None]:
    """For each column the dashboard needs, the closest column in the new file (or None)."""
    names = [c.name for c in new]
    out: dict[str, str | None] = {}
    for c in old:
        hit = process.extractOne(c.name, names, scorer=fuzz.WRatio) if names else None
        out[c.name] = hit[0] if hit and hit[1] >= 70 else None
    return out


def refresh(
    db: Any, dash: DashboardRow, workspace_id: UUID, upload_id: UUID, author: UUID
) -> dict[str, Any]:
    """Attach a new upload to a live dashboard: append → schema check → re-query."""
    upload = db.get(Upload, upload_id)
    if upload is None or upload.workspace_id != workspace_id:
        raise LookupError("Upload not found.")
    cur = store.version(db, dash)
    binding = cur.spec.datasets[0]
    if binding.mode != "live":
        raise AgentError("This dashboard is a snapshot; it doesn't take new data.")
    dataset = db.get(DatasetRow, binding.dataset_id)
    ver = latest_version(db, binding.dataset_id)
    assert dataset is not None
    assert ver is not None
    if upload.status != "done" or str(binding.dataset_id) not in upload.dataset_ids:
        old = [ColumnSchema.model_validate(c) for c in ver.columns]
        new_cols = _upload_columns(upload)
        return {"status": "mapping_needed", "error": upload.error,
                "mapping": proposed_mapping(new_cols, old) if new_cols else {}}  # fmt: skip
    new_cols = [ColumnSchema.model_validate(c) for c in ver.columns]
    if ver.schema_hash != binding.schema_hash:
        if not is_superset(new_cols, [c for c in new_cols if c.name in _spec_fields(cur.spec)]):
            return {"status": "mapping_needed", "mapping": {}}
        spec = cur.spec.model_copy(
            update={"datasets": [binding.model_copy(update={"schema_hash": schema_hash(new_cols)})]}
        )
        v = store.save_spec(
            db, dash.id, workspace_id, spec, author, "Refreshed with new data (columns added)"
        )
        version = v.version
    else:
        version = cur.version
    data = store.widget_data(db, store.version(db, dash).spec)
    return {"status": "refreshed", "version": version, "dataset_version": ver.version,
            "widgets": {k: (len(v.rows) if hasattr(v, "rows") else v) for k, v in data.items()}}  # fmt: skip


def _spec_fields(spec: DashboardSpec) -> set[str]:
    return {f for w in spec.widgets for f in w.query.dimensions}


def _upload_columns(upload: Upload) -> list[ColumnSchema]:
    """Best-effort columns of an upload that couldn't be appended (for the mapping proposal)."""
    import tempfile
    from pathlib import Path

    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / storage.safe_name(upload.filename)
            storage.get_file(upload.object_key, src)
            d = detect(upload.filename, src.read_bytes()[:HEAD_BYTES])
            parsed = parse(src, d, ParseContext(str(upload.id), upload.filename, None, 1, "UTC"))
            first = next(parsed.batches)
            return columns_of(first.schema)
    except Exception:  # mapping is a convenience; never fail the request over it
        return []


# ----------------------------------------------------------------------------- tools


class NoArgs(BaseModel):
    pass


class ResolveIn(BaseModel):
    reference: str | None = Field(default=None, description="The user's words for the widget(s)")
    selected_ids: list[str] = Field(default_factory=list)


class PatchIn(BaseModel):
    ops: list[WidgetPatchOp] = Field(
        min_length=1, description="Widget-id patch ops, e.g. replace /widgets/<id>/type"
    )
    note: str = Field(min_length=1, max_length=300, description="A short change note")


class RevertIn(BaseModel):
    version: int | None = Field(
        default=None, ge=1, description="Version to restore; default: the previous one"
    )


class SearchIn(BaseModel):
    text: str | None = None


@tool(
    "build_dashboard",
    "Compile the approved plan into a saved dashboard; returns its id and URL.",
    NoArgs,
)
def _build(a: Agent, args: NoArgs) -> Any:
    dash = build(a)
    return {"dashboard_id": str(dash.id), "url": f"/d/{dash.id}", "version": 1}


@tool("get_dashboard", "Read the current dashboard spec (widgets, layout, measures).", NoArgs)
def _get(a: Agent, args: NoArgs) -> Any:
    _, cur = current(a)
    return {"version": cur.version, "spec": cur.spec.model_dump(mode="json", exclude_none=True)}


@tool(
    "resolve_widget",
    "Find which widget(s) the user means, from selected ids or their words.",
    ResolveIn,
)
def _resolve(a: Agent, args: ResolveIn) -> Any:
    _, cur = current(a)
    hits = resolve_widgets(cur.spec, args.reference, args.selected_ids or a.selected)
    found = [{"id": w.id, "title": w.title, "type": w.type.value} for w in hits]
    if len(found) > 1:
        return {
            "ambiguous": True,
            "candidates": found,
            "message": "Ask the user which one they mean.",
        }
    return {"ambiguous": False, "widgets": found}


@tool(
    "patch_dashboard",
    "Propose an edit as widget-id patch ops. It is validated and previewed; the user applies it.",
    PatchIn,
)
def _patch(a: Agent, args: PatchIn) -> Any:
    return propose_edit(a, args.ops, args.note)


@tool("revert_dashboard", "Undo: restore an earlier version (as a new version).", RevertIn)
def _revert(a: Agent, args: RevertIn) -> Any:
    dash, cur = current(a)
    target = args.version or cur.version - 1
    if target < 1:
        raise AgentError("There's no earlier version to go back to.")
    v = store.restore(a.db, dash.id, a.principal.workspace_id, target, a.principal.user_id)
    if a.state == State.EDIT:
        a.transition(State.PUBLISHED)
    a.commit()
    events.publish(a.session.id, "dashboard", {"dashboard_id": str(dash.id), "version": v.version})
    return {"restored": target, "version": v.version}


@tool("list_dashboards", "Search saved dashboards by name, domain or tag.", SearchIn)
def _list(a: Agent, args: SearchIn) -> Any:
    stmt = select(DashboardRow).where(DashboardRow.workspace_id == a.principal.workspace_id)
    if args.text:
        like = f"%{args.text}%"
        stmt = stmt.where(or_(DashboardRow.title.ilike(like), DashboardRow.domain.ilike(like)))
    rows = a.db.scalars(stmt.order_by(DashboardRow.updated_at.desc()).limit(20)).all()
    return [
        {"id": str(d.id), "title": d.title, "domain": d.domain, "version": d.current_version}
        for d in rows
    ]
