"""Decisions: what the user must settle before a plan can be approved (R5, rule 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.models.chart import OptionStatus
from app.models.plan import Decision, DecisionChoice, DraftPlan
from app.models.verdict import Verdict, VerdictKind


class DecisionError(ValueError):
    """A choice that isn't allowed for this decision. The message is meant for users."""


def options_for(verdict: Verdict, buildable_as_asked: bool) -> list[DecisionChoice]:
    if verdict.kind == VerdictKind.ADJUSTED:
        opts = [DecisionChoice.ACCEPT_ALTERNATIVE]
        if buildable_as_asked:
            opts.append(DecisionChoice.KEEP_AS_ASKED)
        return [*opts, DecisionChoice.DROP]
    if verdict.kind == VerdictKind.NOT_FEASIBLE:
        opts = [DecisionChoice.ACCEPT_ALTERNATIVE] if verdict.alternatives else []
        return [*opts, DecisionChoice.DROP, DecisionChoice.PROVIDE_DATA]
    if verdict.kind == VerdictKind.NEEDS_CLARIFICATION:
        return [DecisionChoice.CHOOSE_CANDIDATE, DecisionChoice.DROP]
    return []


def decision_for(verdict: Verdict, item: str, buildable_as_asked: bool) -> Decision | None:
    """One decision per adjusted, not_feasible or needs_clarification verdict; None if feasible."""
    if verdict.kind == VerdictKind.FEASIBLE:
        return None
    change = verdict.applied_change
    return Decision(
        id=f"D_{item}"[:64],
        item=item,
        reason=" ".join(verdict.reasons),
        options=options_for(verdict, buildable_as_asked),
        candidates=list(verdict.candidates),
        before=change.before if change else None,
        after=change.after if change else None,
    )


def add_decisions(plan: DraftPlan, decisions: list[Decision]) -> DraftPlan:
    existing = {d.id for d in plan.decisions}
    fresh = [d for d in decisions if d.id not in existing]
    return plan.model_copy(update={"decisions": [*plan.decisions, *fresh]})


def resolve(
    plan: DraftPlan,
    decision_id: str,
    choice: DecisionChoice,
    user_id: UUID,
    *,
    detail: str | None = None,
    now: datetime | None = None,
) -> DraftPlan:
    """Record the user's choice and apply its effect on the plan's options."""
    decision = next((d for d in plan.decisions if d.id == decision_id), None)
    if decision is None:
        raise DecisionError("That decision doesn't exist.")
    if not decision.is_open:
        raise DecisionError("That decision has already been made.")
    if choice not in decision.options:
        if choice == DecisionChoice.KEEP_AS_ASKED:
            raise DecisionError("This chart can't be built as asked, so it can't be kept that way.")
        raise DecisionError(
            f"'{choice.value.replace('_', ' ')}' isn't an option for this decision."
        )
    if choice == DecisionChoice.CHOOSE_CANDIDATE and not detail:
        raise DecisionError("Pick one of the suggested options.")

    note = detail
    if choice == DecisionChoice.PROVIDE_DATA:
        note = "waiting for data"
    decided = decision.model_copy(
        update={
            "choice": choice,
            "choice_detail": note,
            "decided_by": user_id,
            "decided_at": now or datetime.now(UTC),
        }
    )
    options = []
    for o in plan.options:
        if o.id == decision.item or o.request_id == decision.item:
            if choice in (DecisionChoice.DROP, DecisionChoice.PROVIDE_DATA):
                o = o.model_copy(update={"status": OptionStatus.EXCLUDED})
            elif choice in (DecisionChoice.ACCEPT_ALTERNATIVE, DecisionChoice.KEEP_AS_ASKED):
                o = o.model_copy(update={"status": OptionStatus.REQUESTED})
            # TODO(M8): KEEP_AS_ASKED rebuilds the original request; CHOOSE_CANDIDATE re-checks with the pick.
        options.append(o)
    decisions = [decided if d.id == decision_id else d for d in plan.decisions]
    return plan.model_copy(update={"options": options, "decisions": decisions})


def can_build(plan: DraftPlan) -> tuple[bool, list[str]]:
    """(no open decisions, ids of the open ones)."""
    open_ids = [d.id for d in plan.open_decisions]
    return (not open_ids, open_ids)
