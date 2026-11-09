"""The session agent: state transitions, proposal, chat turns, approval and decisions."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.core.security import Redactor, data_block
from app.db.tables import DashboardRow, DatasetRow, DatasetVersionRow, Message, SessionRow, Upload
from app.feasibility.checks import check_request
from app.feasibility.decisions import DecisionError, can_build, decision_for, resolve
from app.models.chart import ChartRequest, FieldPhrase, OptionStatus, VisualOption
from app.models.dataset import ColumnSchema, DatasetProfile
from app.models.plan import (
    AddOp,
    AnnotateOp,
    ChangeEncodingOp,
    ChangeTypeOp,
    DecisionChoice,
    DeriveMetricOp,
    DraftPlan,
    FilterOp,
    LayoutOp,
    PlanOp,
    RemoveOp,
    StyleOp,
)
from app.models.query import Measure, SemanticQuery
from app.models.verdict import VerdictKind
from app.orchestrator import events, plans
from app.orchestrator.llm import LLM, Block, ToolCall, run_tool_loop
from app.orchestrator.states import TOOLS_BY_STATE, State, check_transition
from app.orchestrator.tasks import AGENT_SYSTEM, confirm_domain, curate, parse_turn
from app.profiling.llm_view import llm_view
from app.profiling.service import latest_version, profile_version
from app.query.compiler import QueryError
from app.query.executor import Target, execute
from app.recommend.context import context_for, violations
from app.recommend.engine import recommend

log = logging.getLogger(__name__)
PREVIEW_ROWS = 20
SAMPLE_ROWS = 50  # design rule 1: at most 50 masked sample rows reach the LLM


class AgentError(ValueError):
    """Something the user asked for that can't happen now. The message is meant for users."""


@dataclass
class Tool:
    name: str
    description: str
    input: type[BaseModel]
    run: Callable[[Agent, Any], Any]

    def schema(self) -> Block:
        schema = self.input.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


TOOLS: dict[str, Tool] = {}


def tool(
    name: str, description: str, input_model: type[BaseModel]
) -> Callable[[Callable[[Agent, Any], Any]], Callable[[Agent, Any], Any]]:
    def register(fn: Callable[[Agent, Any], Any]) -> Callable[[Agent, Any], Any]:
        TOOLS[name] = Tool(name, description, input_model, fn)
        return fn

    return register


class Agent:
    def __init__(self, db: Session, principal: Principal, llm: LLM, session: SessionRow) -> None:
        self.db = db
        self.principal = principal
        self.llm = llm
        self.session = session
        self.redactor = Redactor(dict(session.redactions or {}))
        self._turn_requests: dict[str, ChartRequest] = {}
        self.selected: list[str] = []
        self.last_user_text: str | None = None

    # ------------------------------------------------------------------ construction
    @classmethod
    def load(cls, db: Session, principal: Principal, llm: LLM, session_id: UUID) -> Agent:
        row = db.get(SessionRow, session_id)
        if row is None or row.workspace_id != principal.workspace_id:
            raise LookupError("Session not found.")
        return cls(db, principal, llm, row)

    @classmethod
    def start(
        cls, db: Session, principal: Principal, llm: LLM, dashboard_id: UUID | None = None
    ) -> Agent:
        state = State.INGEST
        if dashboard_id is not None:
            dash = db.get(DashboardRow, dashboard_id)
            if dash is None or dash.workspace_id != principal.workspace_id:
                raise LookupError("Dashboard not found.")
            state = State.PUBLISHED  # reopened from the library: edit by chat
        row = SessionRow(workspace_id=principal.workspace_id, user_id=principal.user_id, state=state.value,
                         dashboard_id=dashboard_id)  # fmt: skip
        db.add(row)
        db.commit()
        return cls(db, principal, llm, row)

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> State:
        return State(self.session.state)

    def transition(self, target: State) -> None:
        if target == self.state:
            return
        check_transition(self.state, target)
        self.session.state = target.value
        self.db.flush()
        events.publish(self.session.id, "state", {"state": target.value})

    def tools_for_state(self) -> list[Block]:
        return [TOOLS[n].schema() for n in TOOLS_BY_STATE[self.state] if n in TOOLS]

    def commit(self) -> None:
        self.session.redactions = dict(self.redactor.mapping)
        self.db.commit()

    # ------------------------------------------------------------------ data
    def datasets(self) -> list[tuple[DatasetRow, DatasetVersionRow]]:
        uploads = self.db.scalars(
            select(Upload)
            .where(Upload.session_id == self.session.id, Upload.status == "done")
            .order_by(Upload.created_at)
        ).all()
        seen: dict[str, tuple[DatasetRow, DatasetVersionRow]] = {}
        for u in uploads:
            for did in u.dataset_ids:
                ds = self.db.get(DatasetRow, UUID(did))
                version = latest_version(self.db, UUID(did)) if ds else None
                if ds is not None and version is not None:
                    seen[did] = (ds, version)
        return list(seen.values())

    def profiles(self) -> list[DatasetProfile]:
        return [
            confirm_domain(self.llm, profile_version(self.db, v), self.redactor)
            for _, v in self.datasets()
        ]

    def target(self, index: int = 0) -> Target:
        _, v = self.datasets()[index]
        return Target(
            version_id=v.id,
            parts=v.parts,
            columns=[ColumnSchema.model_validate(c) for c in v.columns],
        )

    def plan_row(self) -> Any:
        return plans.plan_row(self.db, self.session)

    def plan(self) -> DraftPlan:
        row = self.plan_row()
        if row is None:
            raise AgentError("There's no plan yet. Upload a file first.")
        return plans.load(self.db, row)

    def save_plan(self, new: DraftPlan, *, old: DraftPlan | None = None) -> list[str]:
        row = self.plan_row()
        assert row is not None
        if self.state == State.APPROVED:  # any change after approval returns to REFINE
            self.transition(State.REFINE)
        saved = plans.save(self.db, row, new)
        changes = plans.diff(old, saved)
        events.publish(self.session.id, "plan_diff", {"version": saved.version, "changes": changes})
        return changes

    # ------------------------------------------------------------------ proposal
    def propose(self) -> DraftPlan | None:
        """INGEST → PROFILE → PROPOSE once the session's uploads are ingested."""
        if self.state not in (State.INGEST, State.PROFILE) or not self.datasets():
            return None
        self.transition(State.PROFILE)
        events.publish(self.session.id, "tool_progress", {"step": "profiling"})
        profiles = self.profiles()
        targets = self._targets_from_context()
        candidates = recommend(profiles, k=25, targets=targets)
        samples = self._sample_rows()
        options = curate(self.llm, candidates, profiles, redactor=self.redactor, samples=samples)
        plan = DraftPlan(id=uuid4(), session_id=self.session.id, version=1, options=options)
        row = self.plan_row()
        if row is None:
            plans.create(self.db, self.session, plan)
        else:
            self.save_plan(plan, old=plans.load(self.db, row))
        self.transition(State.PROPOSE)
        events.publish(
            self.session.id, "options", {"options": [o.model_dump(mode="json") for o in options]}
        )
        self.commit()
        return plan

    def _targets_from_context(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for entry in self.session.context or []:
            for t in entry.get("thresholds", []):
                value = float(t["value"])
                out[str(t["metric"])] = (
                    value / 100 if t.get("unit") == "%" else value
                )  # percent measures are 0-1
        return out

    def _sample_rows(self) -> list[dict[str, object]]:
        try:
            t = self.target()
        except IndexError:
            return []
        cols = [c.name for c in t.columns][:20]
        res = execute(
            t, SemanticQuery(dataset="ds", dimensions=cols, limit=SAMPLE_ROWS), [], use_cache=True
        )
        return [dict(zip(res.columns, r, strict=False)) for r in res.rows[:SAMPLE_ROWS]]

    # ------------------------------------------------------------------ chat
    def handle_message(self, text: str, selected_widget_ids: list[str] | None = None) -> str:
        self._persist("user", {"text": text, "selected": selected_widget_ids or []})
        if self.state in (State.INGEST, State.PROFILE) and self.propose() is None:
            reply = "Upload a log or results file to get started."
            self._reply(reply)
            return reply
        if self.state == State.PROPOSE:
            self.transition(State.REFINE)
        if self.state == State.PUBLISHED:  # chat on a published dashboard is an edit
            self.transition(State.EDIT)
        self.selected = list(selected_widget_ids or [])
        self.last_user_text = text

        history = self._llm_history()
        turn = {
            "role": "user",
            "content": [{"type": "text", "text": self._turn_text(text, selected_widget_ids)}],
        }
        self._persist("llm_user", turn)
        result = run_tool_loop(
            self.llm, task="converse", state=lambda: self.state.value, system=AGENT_SYSTEM,
            history=[*history, turn], tools_for=self.tools_for_state, execute=self._execute,
            on_call=self._on_call,
        )  # fmt: skip
        for m in result.new_messages:
            self._persist("llm_assistant" if m["role"] == "assistant" else "llm_tool", m)
        reply = self.redactor.restore(result.text)
        if self.state == State.EDIT and not self.session.pending_edit:
            self.transition(State.PUBLISHED)  # nothing to apply: back to the published dashboard
        self._reply(reply, usage=result.usage.model_dump())
        return reply

    def _turn_text(self, text: str, selected: list[str] | None) -> str:
        state: dict[str, Any] = {"state": self.state.value}
        row = self.plan_row()
        if row is not None:
            plan = plans.load(self.db, row)
            state["options"] = [
                {"id": o.id, "title": o.title, "chart": o.chart_type.value, "status": o.status.value}
                for o in plan.options if o.status != OptionStatus.OPTIONAL
            ]  # fmt: skip
            state["open_decisions"] = [
                {"id": d.id, "item": d.item, "reason": d.reason} for d in plan.open_decisions
            ]
        if selected:
            state["selected_widgets"] = selected
        return f"<user_message>\n{self.redactor.redact(text)}\n</user_message>\n{data_block('session', state, self.redactor)}"

    def _llm_history(self) -> list[Block]:
        rows = self.db.scalars(
            select(Message).where(Message.session_id == self.session.id, Message.role.in_(["llm_user", "llm_assistant", "llm_tool"]))
            .order_by(Message.created_at, Message.id)
        ).all()  # fmt: skip
        return [r.content for r in rows]

    def _persist(self, role: str, content: Block) -> None:
        self.db.add(
            Message(
                workspace_id=self.session.workspace_id,
                session_id=self.session.id,
                role=role,
                content=content,
            )
        )
        self.db.flush()

    def _reply(self, text: str, usage: dict[str, int] | None = None) -> None:
        self._persist("assistant", {"text": text, "usage": usage or {}})
        for i in range(0, len(text), 40):
            events.publish(self.session.id, "assistant_delta", {"text": text[i : i + 40]})
        events.publish(self.session.id, "assistant_message", {"text": text})
        self.commit()

    def _on_call(self, call: ToolCall) -> None:
        events.publish(
            self.session.id, "tool_progress", {"tool": call.name, "ok": not call.is_error}
        )

    def _execute(self, name: str, raw: dict[str, Any]) -> tuple[Any, bool]:
        spec = TOOLS[name]
        try:
            args = spec.input.model_validate(self.redactor.restore_value(raw))
        except ValidationError as exc:
            return f"Invalid input: {exc.errors()[:3]}", True
        try:
            out = spec.run(self, args)
        except (AgentError, DecisionError, QueryError) as exc:
            return str(exc), True
        return self.redactor.redact_value(out), False

    # ------------------------------------------------------------------ approval & decisions
    def approve(self) -> DraftPlan:
        """The only way into APPROVED (POST /sessions/{id}/approve, from the user's click)."""
        if self.state not in (State.PROPOSE, State.REFINE):
            raise AgentError(f"There's nothing to approve in the {self.state.value} state.")
        row = self.plan_row()
        plan = plans.load(self.db, row)
        ok, open_ids = can_build(plan)
        if not ok:
            raise AgentError(f"{len(open_ids)} item(s) still need your decision before approval.")
        if not plans.included(plan):
            raise AgentError("The plan has no charts to build.")
        self.transition(State.APPROVED)
        row.approved_version = row.current_version
        self.commit()
        return plan

    def decide(
        self, decision_id: str, choice: DecisionChoice, detail: str | None = None
    ) -> DraftPlan:
        old = self.plan()
        plan = resolve(old, decision_id, choice, self.principal.user_id, detail=detail)
        decision = next(d for d in plan.decisions if d.id == decision_id)
        plan = self._apply_choice(plan, decision.item, choice, detail)
        self.save_plan(plan, old=old)
        events.publish(self.session.id, "decision", {"id": decision_id, "choice": choice.value})
        self.commit()
        return self.plan()

    def _apply_choice(
        self, plan: DraftPlan, item: str, choice: DecisionChoice, detail: str | None
    ) -> DraftPlan:
        request = next((r for r in plan.requests if r.id == item or f"req_{r.id}" == item), None)
        options = list(plan.options)
        if choice == DecisionChoice.KEEP_AS_ASKED:
            options = [
                o.model_copy(update={"status": OptionStatus.REQUESTED}) if o.id == f"{item}_asked"
                else o.model_copy(update={"status": OptionStatus.EXCLUDED}) if o.id == item
                else o
                for o in options
            ]  # fmt: skip
        elif request is not None and choice in (
            DecisionChoice.ACCEPT_ALTERNATIVE,
            DecisionChoice.CHOOSE_CANDIDATE,
        ):
            if choice == DecisionChoice.CHOOSE_CANDIDATE:
                assert detail is not None
                phrases = [
                    FieldPhrase(text=detail, role=p.role) for p in request.field_phrases[:1]
                ] or [FieldPhrase(text=detail)]
                new_req = request.model_copy(
                    update={"id": f"{request.id}_c", "field_phrases": phrases}
                )
            else:
                verdict_alt = check_request(
                    request, context_for(self.profiles()[0])
                ).verdict.alternatives
                if not verdict_alt:
                    return plan
                new_req = verdict_alt[0]
            result = check_request(new_req, context_for(self.profiles()[0]))
            if result.option is not None and result.option.id not in {o.id for o in options}:
                options.append(result.option.model_copy(update={"status": OptionStatus.REQUESTED}))
        return plan.model_copy(update={"options": options})

    # ------------------------------------------------------------------ feasibility into the plan
    def record_request(self, request: ChartRequest) -> dict[str, Any]:
        """check_feasibility: verdict + plan update (the request, its option and its decision)."""
        old = self.plan()
        profiles = self.profiles()
        ctx = context_for(profiles[0], targets=self._targets_from_context())
        result = check_request(request, ctx)
        options = [o for o in old.options if o.request_id != request.id]
        decisions = list(old.decisions)
        item = request.id
        if result.option is not None:
            options.append(result.option)
            item = result.option.id
            if result.verdict.kind == VerdictKind.ADJUSTED and result.buildable_as_asked:
                asked = self._as_asked(request, ctx)
                if asked is not None:
                    options.append(
                        asked.model_copy(
                            update={"id": f"{item}_asked", "status": OptionStatus.EXCLUDED}
                        )
                    )
        decision = decision_for(result.verdict, item, result.buildable_as_asked)
        if decision is not None:
            decisions.append(decision.model_copy(update={"id": plans.new_decision_id()}))
        requests = [r for r in old.requests if r.id != request.id] + [request]
        new = old.model_copy(
            update={"options": options, "decisions": decisions, "requests": requests}
        )
        changes = self.save_plan(new, old=old)
        if decision is not None:
            events.publish(self.session.id, "approval_card", {
                "decision_id": decisions[-1].id, "item": item, "reason": decision.reason,
                "options": [c.value for c in decision.options],
                "before": result.verdict.applied_change.before if result.verdict.applied_change else None,
                "after": result.verdict.applied_change.after if result.verdict.applied_change else None,
            })  # fmt: skip
        return {
            "verdict": result.verdict.kind.value, "reasons": result.verdict.reasons,
            "option_id": result.option.id if result.option else None,
            "alternatives": [a.raw_text for a in result.verdict.alternatives],
            "candidates": result.verdict.candidates, "changes": changes,
        }  # fmt: skip

    def _as_asked(self, request: ChartRequest, ctx: Any) -> VisualOption | None:
        """The original, unadjusted version of a buildable adjusted request (for keep_as_asked)."""
        chart = request.widget_type()
        res = check_request(request.model_copy(update={"top_n": request.top_n}), ctx)
        if res.option is None or chart is None:
            return None
        q = res.option.query.model_copy(update={"top_n": request.top_n, "limit": None})
        return res.option.model_copy(update={"chart_type": chart, "query": q})

    # ------------------------------------------------------------------ plan ops
    def apply_ops(self, ops: list[PlanOp]) -> list[str]:
        old = self.plan()
        plan = old
        ctx = context_for(self.profiles()[0])
        for op in ops:
            plan = _apply_op(plan, op, ctx)
        return self.save_plan(plan, old=old)


def _apply_op(plan: DraftPlan, op: PlanOp, ctx: Any) -> DraftPlan:
    options = {o.id: o for o in plan.options}

    def need(option_id: str) -> VisualOption:
        if option_id not in options:
            raise AgentError(f"There's no chart {option_id!r} in the plan.")
        return options[option_id]

    match op:
        case AddOp(option=o):
            if violations(o, ctx):
                raise AgentError(f"{o.title} breaks a chart rule: {', '.join(violations(o, ctx))}.")
            options[o.id] = o.model_copy(update={"status": OptionStatus.REQUESTED})
        case RemoveOp(option_id=oid):
            options[oid] = need(oid).model_copy(update={"status": OptionStatus.EXCLUDED})
        case ChangeTypeOp(option_id=oid, chart_type=t):
            new = need(oid).model_copy(update={"chart_type": t})
            if problems := violations(new, ctx):
                raise AgentError(
                    f"A {t.value.replace('_', ' ')} doesn't work here: {', '.join(problems)}."
                )
            options[oid] = new
        case ChangeEncodingOp(option_id=oid):
            o = need(oid)
            q = o.query
            measures = list(o.measures)
            if op.measures is not None:
                measures = op.measures
                q = q.model_copy(update={"measures": [m.id for m in op.measures]})
            if op.dimensions is not None:
                q = q.model_copy(update={"dimensions": op.dimensions})
            new = o.model_copy(update={"query": SemanticQuery.model_validate(q.model_dump()), "measures": measures,
                                       "encoding": op.encoding or o.encoding})  # fmt: skip
            if problems := violations(new, ctx):
                raise AgentError(f"That change breaks a chart rule: {', '.join(problems)}.")
            options[oid] = new
        case FilterOp(option_id=None, filter=f):
            from app.models.spec import GlobalFilter

            gf = GlobalFilter(
                id=f"f_{f.field}"[:64].replace(".", "_"), field=f.field, control="multiselect"
            )
            return plan.model_copy(
                update={"filters": [*[x for x in plan.filters if x.id != gf.id], gf]}
            )
        case FilterOp(option_id=oid, filter=f) if oid is not None:
            o = need(oid)
            options[oid] = o.model_copy(
                update={"query": o.query.model_copy(update={"filters": [*o.query.filters, f]})}
            )
        case DeriveMetricOp(measure=m):
            return plan.model_copy(
                update={"measures": [*[x for x in plan.measures if x.id != m.id], m]}
            )
        case LayoutOp(hints=h):
            return plan.model_copy(update={"layout_hints": h})
        case StyleOp(option_id=oid, thresholds=t):
            options[oid] = need(oid).model_copy(update={"thresholds": t})
        case AnnotateOp(option_id=oid, annotation=a):
            o = need(oid)
            options[oid] = o.model_copy(update={"annotations": [*o.annotations, a]})
    return plan.model_copy(update={"options": list(options.values())})


# ============================================================================ tools (R7)


class NoInput(BaseModel):
    pass


class DatasetIn(BaseModel):
    dataset_index: int = Field(
        default=0, ge=0, description="Which of the session's datasets (0 = first)"
    )


class QueryIn(BaseModel):
    query: SemanticQuery
    measures: list[Measure] = Field(default_factory=list)
    dataset_index: int = 0


class RecommendIn(BaseModel):
    intent: str | None = None


class ParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="The user's message, verbatim")


class CheckIn(BaseModel):
    request: ChartRequest


class OpsIn(BaseModel):
    ops: list[dict[str, Any]] = Field(min_length=1, description="Plan operations (see PlanOp)")


class PreviewIn(BaseModel):
    option_id: str


class ApprovalIn(BaseModel):
    approve: bool = Field(default=False, description="True when the user asked to approve or build")


class FilesIn(BaseModel):
    file_ids: list[str] = Field(default_factory=list)


_OPS = TypeAdapter(list[PlanOp])


@tool(
    "ingest_files",
    "Report the ingestion status of uploaded files and the dataset ids they produced.",
    FilesIn,
)
def _ingest_files(a: Agent, args: FilesIn) -> Any:
    uploads = a.db.scalars(select(Upload).where(Upload.session_id == a.session.id)).all()
    return [{"id": str(u.id), "file": u.filename, "status": u.status, "error": u.error, "datasets": u.dataset_ids}
            for u in uploads if not args.file_ids or str(u.id) in args.file_ids]  # fmt: skip


@tool("profile_dataset", "Return the compact profile of one of the session's datasets.", DatasetIn)
def _profile(a: Agent, args: DatasetIn) -> Any:
    profiles = a.profiles()
    if args.dataset_index >= len(profiles):
        raise AgentError("There's no dataset with that index.")
    return llm_view(profiles[args.dataset_index])


@tool(
    "query",
    "Run a semantic query for your own reasoning. Aggregated rows (max 500); raw rows are capped at 50.",
    QueryIn,
)
def _query(a: Agent, args: QueryIn) -> Any:
    q = args.query
    cap = 500 if q.measures else SAMPLE_ROWS
    q = q.model_copy(update={"limit": min(q.limit or cap, cap)})
    res = execute(a.target(args.dataset_index), q, args.measures)
    return {"columns": res.columns, "rows": res.rows[:cap], "truncated": res.truncated}


@tool("recommend_visuals", "Ranked chart candidates (top 25) with score breakdowns.", RecommendIn)
def _recommend(a: Agent, args: RecommendIn) -> Any:
    return [{"id": o.id, "chart": o.chart_type.value, "title": o.title, "score": o.score.total if o.score else None,
             "facts": o.score.facts if o.score else []} for o in recommend(a.profiles(), k=25)]  # fmt: skip


@tool(
    "parse_chart_request",
    "Turn the user's message into chart requests and plan operations.",
    ParseIn,
)
def _parse(a: Agent, args: ParseIn) -> Any:
    turn = len(a._llm_history()) + 1
    plan = a.plan()
    requests, parsed = parse_turn(a.llm, args.text, state=a.state.value, options=plan.options,
                                  redactor=a.redactor, turn=turn)  # fmt: skip
    for r in requests:
        a._turn_requests[r.id] = r
    return {
        "requests": [r.model_dump(mode="json", exclude_none=True) for r in requests],
        "ops": [o.model_dump(mode="json") for o in parsed.ops],
        "approve": parsed.approve, "undo": parsed.undo, "explain": parsed.explain,
    }  # fmt: skip


@tool(
    "check_feasibility",
    "Check a chart request against the data and add it to the plan (with a decision if it needs one).",
    CheckIn,
)
def _check(a: Agent, args: CheckIn) -> Any:
    return a.record_request(args.request)


@tool(
    "update_plan",
    "Apply plan operations (remove, change_type, change_encoding, filter, derive_metric, layout, style, annotate).",
    OpsIn,
)
def _update(a: Agent, args: OpsIn) -> Any:
    try:
        ops = _OPS.validate_python(args.ops)
    except ValidationError as exc:
        raise AgentError(f"Those plan operations aren't valid: {exc.errors()[:3]}") from None
    return {"changes": a.apply_ops(ops)}


@tool("render_preview", "Sample rows for one option's thumbnail.", PreviewIn)
def _preview(a: Agent, args: PreviewIn) -> Any:
    option = next((o for o in a.plan().options if o.id == args.option_id), None)
    if option is None:
        raise AgentError(f"There's no chart {args.option_id!r} in the plan.")
    res = execute(
        a.target(), option.query.model_copy(update={"limit": PREVIEW_ROWS}), option.measures
    )
    return {"columns": res.columns, "rows": res.rows[:PREVIEW_ROWS]}


@tool(
    "request_approval",
    "Show approval cards for open decisions, or a confirm card when the user wants to approve. You cannot approve yourself.",
    ApprovalIn,
)
def _request_approval(a: Agent, args: ApprovalIn) -> Any:
    plan = a.plan()
    ok, open_ids = can_build(plan)
    for d in plan.open_decisions:
        events.publish(a.session.id, "approval_card", {"decision_id": d.id, "item": d.item, "reason": d.reason,
                                                        "options": [c.value for c in d.options]})  # fmt: skip
    if args.approve and ok:
        events.publish(
            a.session.id,
            "approval_confirm",
            {"plan_version": plan.version, "charts": len(plans.included(plan))},
        )
        return "A confirm card is shown. The plan is approved only when the user clicks Approve."
    if args.approve:
        return f"Approval is blocked: {len(open_ids)} decision(s) are still open. Their cards are shown."
    return f"{len(open_ids)} decision card(s) shown."


def dump(obj: Any) -> str:
    return json.dumps(obj, default=str)


from app.orchestrator import dashboards as _dashboard_tools  # noqa: E402,F401 - registers M9 tools
