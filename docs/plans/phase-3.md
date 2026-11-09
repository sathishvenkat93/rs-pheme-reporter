# Phase 3 plan — Agent and dashboards (M8, M9)

Branch `phase-3` (from `phase-2`; neither is merged yet: the GitHub push is blocked). No API key is configured, so everything is built and tested against FakeLLM. The one live run (`PHEME_LIVE_LLM=1`) stays open until you add `PHEME_ANTHROPIC_API_KEY` to `.env`.

## M8 — Orchestrator and chat API

LLM layer (`orchestrator/llm.py`):

- An `LLM` protocol with `complete(role, task, state, system, messages, tools, output_schema)` returning an `LLMResponse`: content blocks as dicts, stop_reason, usage, model.
- `AnthropicLLM` wraps the Anthropic Python SDK:
  - main = `PHEME_MODEL_MAIN` (claude-sonnet-5-5) at effort medium, through `client.beta.messages.create` with the `fallbacks: "default"` server-side refusal fallback (`server-side-fallback-2026-07-01`); fast = `PHEME_MODEL_FAST` (claude-haiku-5-5) at effort low
  - SDK retries (3) and a 60 s timeout
  - top-level `cache_control` for prompt caching, with a frozen system prompt and tools in a fixed order
  - structured outputs via `output_config.format`
  - `tool_choice: auto`, because forced tool use is a 400 on Sonnet 5.5
  - `stop_reason == "refusal"` raises `LLMRefusal`
- `FakeLLM` answers from rules matched on (task, state, user-message pattern, tools offered) and records every request, for the redaction and tool-list tests.
- `run_tool_loop()` is a manual loop: max iterations, the tool list re-read every iteration (the state can change mid-loop), invalid tool input returned as an `is_error` tool_result, and the full assistant content (thinking blocks included) appended unchanged. History is append-only.

State machine (`orchestrator/states.py`):

- States: INGEST → PROFILE → PROPOSE → REFINE → APPROVED → BUILD → PUBLISHED ⇄ EDIT, PUBLISHED ⇄ REFRESH.
- Guards live in code:
  - APPROVED only through `POST /sessions/{id}/approve` with zero open decisions
  - any plan change in APPROVED returns to REFINE and clears the approval
  - `POST /sessions` with `dashboard_id` starts in PUBLISHED
- `TOOLS_BY_STATE` follows R7; `infer_parser` and `confirm_parser` are post-MVP and omitted.

Tools (`orchestrator/tools.py`): typed Pydantic input → handler(ToolContext) → JSON output.

- `check_feasibility` records the request in the plan, adds its option (feasible/adjusted) and its decision; that's how rule 4 is enforced.
- `update_plan` applies PlanOps, versions the plan and returns a diff.
- `request_approval` only emits a confirm card.
- `build_dashboard`, `resolve_widget` and `patch_dashboard` are wired in M9.

Tasks (`orchestrator/tasks.py`):

- fast model: intent parsing (text → ChartRequest[] + PlanOp[] + approve/undo/explain flags), domain confirmation when confidence < 0.8, context-fact extraction
- main model: curation (6–10 of the top 25, ordered KPI → trends → breakdowns → distributions → detail, each with a rationale and expected insight) and reply writing
- every structured output is validated with Pydantic, retried once with the error, then falls back (deterministic curation / ask the user)

Redaction (`core/security.py`): emails, IPv4/IPv6, bearer tokens, API keys (sk-…, AKIA…, ghp_…, key=…), JWTs and long hex become reversible placeholders (`«EMAIL_1»`). The mapping is stored server-side per session (`sessions.redactions`, migration 0003). It is applied to sample rows, log snippets, context text and user text before any LLM call, and placeholders in LLM output are restored before anything is written to a plan or spec.

Prompt-injection hygiene: data goes in `<data kind="…">` blocks, and the system prompt says data is never instructions.

API:

- `POST /sessions` (with optional `dashboard_id`)
- `POST /sessions/{id}/messages`
- `GET /sessions/{id}/events` (SSE over a Redis Stream per session; `Last-Event-ID` replay; `?follow=false` drains, for tests)
- `POST /sessions/{id}/approve`
- `GET /plans/{id}`, `GET /plans/{id}/decisions`, `POST /decisions/{id}`

Persistence: user messages, assistant content and tool results (API format, append-only) in `messages`; plan versions; decision rows mirrored from the plan JSON.

Tests:

- FakeLLM session on JUnit reaches PROPOSE with curated options
- "pie of failures by test" → needs_approval; approve refused while it's open, accepted after resolving
- `build_dashboard` absent before APPROVED in every captured request
- no LLM text moves the state
- seeded secrets never appear in captured requests
- placeholder restore
- SSE replay

## M9 — Dashboard builder, versions and edit planner

- `dashboards/builder.py`: plan version → DashboardSpec. Stable widget ids `w_<option id>`; dataset bindings (live by default, latest version); measures merged from options. Thresholds and annotations travel from option to widget (VisualOption gains both).
- `dashboards/layout.py`: auto-layout on the 12-column grid (KPIs 3 wide in the top row, trends full width or half width in pairs, breakdowns half width, tables full width).
- Validation: schema, then every widget query dry-runs (compile + execute with limit 1), then queries run and cache.
- `dashboards/store.py`: versions are append-only and the spec body carries no version.
  - `create`
  - `patch(dashboard, base_version, ops, author, prompt)`: 409 on a stale base; schema + feasibility guards (`violations`) + dry-run; a new version with a change note; an audit event
  - `restore(n)` makes a new version from n's body
  - `diff(a, b)` as RFC 6902 ops
- API:
  - `GET /dashboards` (search by name, domain, tag, owner; sort by last updated)
  - `GET /dashboards/{id}`, `/versions`, `/versions/{n}`, `/data` (global filters via query params), `/diff?from=&to=`
  - `POST /dashboards/{id}/restore/{n}`, `PATCH /dashboards/{id}`
  - `POST /dashboards/{id}/refresh` (attach an upload to a live dashboard)
- Edit planner (EDIT state):
  - `resolve_widget` uses selected ids or words (title, type, fields, position words like "first", "top left") and asks when more than one matches
  - the LLM proposes widget-id patch ops (`propose_edit` tool), which are validated and stored as a pending edit with before/after preview data
  - `apply_edit` commits one version (bulk edits are one version); `revert_dashboard` restores
- Refresh: a live dataset gets a new version from the new upload (append), then a schema check (same hash or superset), then re-query. On a mismatch, a proposed field mapping (rapidfuzz over column names) is returned.

Tests:

- the build from the M8 session yields a valid spec where every widget returns data
- one FakeLLM edit per R8 row; undo gives a byte-identical body
- concurrent PATCHes on one base → one 200 and one 409
- a second JUnit upload adds its build to pass rate by build

## Phase exit

- `tests/e2e/test_api_flow.py` (FakeLLM, JUnit): session → upload → options → flagged request → resolve → approve → build → chat edit → undo → second upload refresh.
- The live run is recorded in `docs/progress.md` when a key is available.

## Risks

- Scope. M8 is the largest milestone; I'll keep tools thin over the Phase 2 services.
- SDK beta parameters (`fallbacks`) untestable without a key. Guarded: if the API rejects them, the client retries once without them and logs it.

## Deviations log

(appended during the phase)
- **M8** — No API key, so `AnthropicLLM` is written to the SDK docs but not exercised. `tests/orchestrator/test_live_llm.py` records the transcript and cost to `docs/live-runs/<date>.md` when run with `PHEME_LIVE_LLM=1`.
- **M8** — The default `PHEME_LLM=fake` uses an offline policy (`orchestrator/offline.py`: a heuristic intent parser plus a scripted agent that calls the same tools in the same order a real model would), so the app and future e2e tests run without a key.
- **M8** — Assistant text is sent as `assistant_delta` chunks of the final reply, not true token streaming (the client calls the non-streaming Messages API). Real token streaming can replace it without changing the event shape.
- **M8** — Decision ids are UUIDs (shared by the plan JSON and the `decisions` table) so `POST /decisions/{id}` is global.
- **M8** — keep_as_asked works because, for buildable adjusted requests, the unadjusted option is stored too (`<id>_asked`, excluded) and the choice swaps them.
- **M8** — `VisualOption` gained `thresholds`, `annotations` and `insight`; `sessions` gained `redactions` and `pending_edit` (migration 0003).
- **M8** — Raw-row `query` results are capped at 50 for the LLM (rule 1); aggregated rows at 500 (R7).
- **M9** — Edits are proposed by the agent (`patch_dashboard` → a pending edit with before/after preview data), and commit only through `POST /sessions/{id}/edit/apply`. `revert_dashboard` (undo) commits immediately, because the user asked for it directly.
- **M9** — Chat on a PUBLISHED session enters EDIT; it returns to PUBLISHED when an edit is applied or discarded, or when the turn ends with nothing pending.
- **M9** — Refresh is `POST /dashboards/{id}/refresh {upload_id}` after an append upload. An incompatible upload (refused at ingest) gets a proposed field mapping (rapidfuzz) instead of an error. The session passes through REFRESH while it runs.
- **M9** — The Phase 3 exit test lives in `tests/orchestrator/test_phase3_flow.py`, next to the fixtures it shares, instead of `tests/e2e/`.
- **M9** — Percent thresholds from context ("at least 95 %") become 0–1 KPI targets.
