"""Plans and decisions."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import DB, Caller, Model
from app.db.tables import DecisionRow, DraftPlanRow
from app.feasibility.decisions import DecisionError
from app.models.plan import Decision, DecisionChoice, DraftPlan
from app.orchestrator import plans
from app.orchestrator.agent import Agent, AgentError

router = APIRouter(tags=["plans"])


class DecideIn(BaseModel):
    choice: DecisionChoice
    detail: str | None = None


def _row(db: DB, caller: Caller, plan_id: UUID) -> DraftPlanRow:
    row = db.get(DraftPlanRow, plan_id)
    if row is None or row.workspace_id != caller.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    return row


@router.get("/plans/{plan_id}", response_model=DraftPlan)
def get_plan(plan_id: UUID, db: DB, caller: Caller) -> DraftPlan:
    return plans.load(db, _row(db, caller, plan_id))


@router.get("/plans/{plan_id}/decisions", response_model=list[Decision])
def get_decisions(plan_id: UUID, db: DB, caller: Caller) -> list[Decision]:
    return plans.load(db, _row(db, caller, plan_id)).decisions


@router.post("/decisions/{decision_id}", response_model=DraftPlan)
def decide(decision_id: UUID, body: DecideIn, db: DB, caller: Caller, llm: Model) -> DraftPlan:
    d = db.get(DecisionRow, decision_id)
    if d is None or d.workspace_id != caller.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found.")
    plan_row = db.get(DraftPlanRow, d.plan_id)
    assert plan_row is not None
    agent = Agent.load(db, caller, llm, plan_row.session_id)
    try:
        return agent.decide(str(decision_id), body.choice, body.detail)
    except (DecisionError, AgentError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
