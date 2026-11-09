"""Draft-plan persistence: every change is a new plan version; decisions are mirrored as rows."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import DecisionRow, DraftPlanRow, PlanVersion, SessionRow
from app.models.chart import OptionStatus
from app.models.plan import Decision, DraftPlan

INCLUDED = frozenset(
    {OptionStatus.RECOMMENDED, OptionStatus.REQUESTED, OptionStatus.NEEDS_APPROVAL}
)


def plan_row(db: Session, session: SessionRow) -> DraftPlanRow | None:
    return db.scalars(select(DraftPlanRow).where(DraftPlanRow.session_id == session.id)).first()


def load(db: Session, row: DraftPlanRow, version: int | None = None) -> DraftPlan:
    v = version or row.current_version
    pv = db.scalars(
        select(PlanVersion).where(PlanVersion.plan_id == row.id, PlanVersion.version == v)
    ).one()
    return DraftPlan.model_validate(pv.plan)


def create(db: Session, session: SessionRow, plan: DraftPlan) -> DraftPlanRow:
    row = DraftPlanRow(
        id=plan.id, workspace_id=session.workspace_id, session_id=session.id, current_version=1
    )
    db.add(row)
    db.flush()
    _write_version(db, row, plan.model_copy(update={"version": 1}))
    return row


def save(db: Session, row: DraftPlanRow, plan: DraftPlan) -> DraftPlan:
    """Store ``plan`` as the next version (append-only)."""
    nxt = plan.model_copy(update={"version": row.current_version + 1})
    _write_version(db, row, nxt)
    row.current_version = nxt.version
    row.approved_version = None  # any change clears an approval (rule: re-approve after changes)
    return nxt


def _write_version(db: Session, row: DraftPlanRow, plan: DraftPlan) -> None:
    db.add(PlanVersion(workspace_id=row.workspace_id, plan_id=row.id, version=plan.version,
                       plan=plan.model_dump(mode="json")))  # fmt: skip
    for d in plan.decisions:
        _mirror(db, row, d)
    db.flush()


def _mirror(db: Session, row: DraftPlanRow, d: Decision) -> None:
    rid = UUID(d.id)
    existing = db.get(DecisionRow, rid)
    if existing is None:
        db.add(DecisionRow(id=rid, workspace_id=row.workspace_id, plan_id=row.id, item=d.item, reason=d.reason,
                           options=[o.value for o in d.options]))  # fmt: skip
        return
    existing.choice = d.choice.value if d.choice else None
    existing.choice_detail = d.choice_detail
    existing.decided_by = d.decided_by
    existing.decided_at = d.decided_at


def new_decision_id() -> str:
    return str(uuid4())


def included(plan: DraftPlan) -> list[str]:
    return [o.id for o in plan.options if o.status in INCLUDED]


def diff(old: DraftPlan | None, new: DraftPlan) -> list[str]:
    """Plain-language change list, e.g. ["Added req_pie", "Changed O2 to bar", "1 item needs approval"]."""
    if old is None:
        return [f"{len(included(new))} charts proposed"]
    before = {o.id: o for o in old.options}
    out: list[str] = []
    for o in new.options:
        prev = before.get(o.id)
        if prev is None:
            if o.status in INCLUDED:
                out.append(f"Added {o.title}")
        elif prev.status in INCLUDED and o.status not in INCLUDED:
            out.append(f"Removed {o.title}")
        elif prev.status not in INCLUDED and o.status in INCLUDED:
            out.append(f"Added {o.title}")
        elif prev.chart_type != o.chart_type:
            out.append(f"Changed {o.title} to {o.chart_type.value.replace('_', ' ')}")
        elif prev.model_dump() != o.model_dump():
            out.append(f"Updated {o.title}")
    if old.filters != new.filters:
        out.append("Updated dashboard filters")
    if old.layout_hints != new.layout_hints:
        out.append("Updated layout")
    new_open = len(new.open_decisions)
    if new_open:
        out.append(
            f"{new_open} item{'s' if new_open > 1 else ''} need{'s' if new_open == 1 else ''} approval"
        )
    return out or ["No changes"]
