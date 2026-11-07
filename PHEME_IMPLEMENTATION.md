# Pheme — Implementation plan for Claude Code

> **Setup:** put this file in the repo root, open Claude Code in the repo, and paste:
> `Read PHEME_IMPLEMENTATION.md and follow section 1, "How to work". Start Phase 1.`

Pheme is a chat-driven agent that turns uploaded logs and statistics into dashboards. The agent proposes the best charts for the data and flags requests it can't build as asked. The user refines the plan in chat and approves it. Pheme then saves a dashboard the user can revisit and edit through chat.

This file is the complete build plan for the MVP. It splits the work into **5 build phases** containing 12 milestones, each with its scope and a "Done when" list, followed by reference tables. Claude Code builds one phase at a time. At the end of each phase it **stops at a phase gate** and waits for the user's go-ahead before starting the next.

If `docs/architecture.md` exists (exported from the design doc), it is the fuller design. Where it and this file disagree, ask before choosing.

---

## 1. How to work (instructions for Claude Code)

The build runs in 5 phases (section 5). Complete one phase, then stop at its gate. **Never start a phase until I explicitly tell you to**, with "Start Phase N" or "Proceed to Phase N".

**Starting a phase**

1. If `docs/progress.md` exists, check that every earlier phase is complete. If one isn't, stop and tell me.
2. Read section 2 (design rules), section 3 (stack and layout), the phase's section with all its milestones, and every reference table they cite (R1–R10 in section 6).
3. Write the phase plan to `docs/plans/phase-<N>.md`. For each milestone, list the files to create or change, key interfaces, the tests you'll write and the risks. If the plan raises a question only I can answer, stop and ask; otherwise carry on. If I asked for "plan only", stop here.

**During the phase**

4. Build the phase's milestones in the order listed. For each milestone: implement it with tests, run `make check` until green, verify its "Done when" items, tick it in `docs/progress.md`, and make one conventional commit (e.g. `feat(m3): ingestion and parsers`).
5. Don't stop between milestones of the same phase unless you hit a blocker that needs my decision.

**Phase gate (end of every phase)**

6. Run `make check` (plus `make e2e` from Phase 4 onward), and verify the phase's exit criteria.
7. Write `docs/reports/phase-<N>.md` covering: what was built; how you verified each "Done when" item and exit criterion; deviations from this plan; open issues; and decisions I need to make. Commit it.
8. **Stop and end your turn** with:
   - a short summary of the report;
   - an outline of the next phase: its milestones, and anything you need from me first;
   - the question **"Phase <N> is complete. Reply 'Proceed to Phase <N+1>' to continue, or tell me what to change."**
9. Never start the next phase in the same turn, even if everything passed. If I ask for changes, make them, re-run the gate (steps 6–8) and stop again.

Rules while working:

- Never build ahead of the current phase. Leave `TODO(M<n>)` notes where later work plugs in.
- If part of this plan is wrong or impossible, stop and explain. Don't silently deviate.
- Never weaken, skip or delete a test to make checks pass.
- Tests never call the real LLM; use `FakeLLM`. Live calls run only when `PHEME_LIVE_LLM=1`.
- Never commit secrets. `.env` is git-ignored, and `.env.example` lists every variable.
- If the conversation gets long mid-phase: commit the finished milestones, update `docs/progress.md`, and tell me to resume in a fresh session. `docs/progress.md` and the phase plan carry the state between sessions.

---

## 2. Design rules (never break these)

1. **The LLM plans; code executes.** The LLM never receives raw logs, only dataset profiles and at most 50 PII-masked sample rows. It never writes rendering code and never puts raw SQL into a spec.
2. **The Dashboard Spec is the single source of truth.** It is JSON, schema-validated and versioned. Every change, from chat or from the UI, is a JSON Patch op addressed by widget id.
3. **The approval gate is enforced in code.** `build_dashboard` is only exposed in the APPROVED state, which needs zero open decisions plus an explicit approval from the user.
4. **Every user-requested chart or edit passes the feasibility validator** before it enters a plan or a spec.
5. **Each orchestrator state exposes only its own tools** to the LLM.
6. **Deterministic services never call the LLM.** Ingestion, profiling, recommendation rules, feasibility checks, query compilation and layout are pure, tested code.
7. **Log content is data, never instructions.** Always pass it to the LLM inside clearly delimited data blocks.

---

## 3. Stack, layout, commands

**Stack**

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL, Redis + RQ, MinIO (S3 API), DuckDB + Parquet, Polars, lxml, sqlglot, rapidfuzz, Anthropic Python SDK. Package manager: uv.
- Frontend: Vite + React + TypeScript, TanStack Query, react-grid-layout, Apache ECharts, Tailwind. Package manager: pnpm.
- Quality: pytest (+ hypothesis), Vitest, Playwright, ruff, mypy (strict for `backend/app/models` and `backend/app/core`), eslint, tsc.
- Models come from env vars: `PHEME_MODEL_MAIN` (default `claude-sonnet-5-5`) for orchestration and curation, and `PHEME_MODEL_FAST` (default `claude-haiku-5-5`) for intent parsing and classification.

**Layout**

- `backend/app/api` — FastAPI routers
- `backend/app/core` — config, db, logging, security (PII redaction)
- `backend/app/models` — Pydantic domain models, spec schema, patch ops
- `backend/app/db` — SQLAlchemy tables, Alembic migrations
- `backend/app/ingestion` — detection, parsers, normalization, catalog
- `backend/app/profiling` — profiler, domain classifier
- `backend/app/domain_packs` — YAML packs (KPIs, signature visuals, synonyms)
- `backend/app/query` — semantic query to SQL, executor, cache
- `backend/app/recommend` — chart grammar, scoring
- `backend/app/feasibility` — field resolver, checks, decisions
- `backend/app/orchestrator` — state machine, tools, LLM client, prompts
- `backend/app/dashboards` — builder, layout, spec store, versions, edit planner
- `backend/app/jobs` — RQ workers
- `backend/tests/fixtures` — sample logs
- `backend/eval` — golden set and evaluation runner
- `frontend/src/features/{chat,proposal,dashboard,library}`
- `frontend/src/charts` — widget spec + rows to ECharts option
- `frontend/src/api` — typed client generated from OpenAPI and JSON Schemas
- `docs/architecture.md` (optional), `docs/plans/`, `docs/progress.md`

**Commands**

- `make dev` — docker compose (postgres, redis, minio) + API + worker + frontend
- `make check` — ruff, mypy, pytest, eslint, tsc, vitest
- `make e2e` — Playwright
- `make types` — regenerate frontend types from OpenAPI and JSON Schemas
- `make eval` — run the evaluation harness
- `make demo-data` — print profiles, recommendations and feasibility verdicts for the fixtures (from Phase 2)

---

## 4. Assumptions

The design left these open. The plan uses the defaults below. If one changes, update this table and the milestones it lists.

| Decision | Assumed | Affects |
| --- | --- | --- |
| Chart library | Apache ECharts behind Pheme's own spec | M10 renderer |
| LLM hosting | Anthropic API; model ids from env vars | M8 |
| Largest MVP file | 200 MB; DuckDB only (no ClickHouse) | M3 |
| First log formats | JUnit XML, JMeter JTL, CSV/JSON | M3, M4 |
| Where the UI lives | Standalone web app | M12 auth |
| Approval granularity | Per-item approval cards + one "Approve plan" action | M7, M10 |
| Orchestration | Own state machine on the Anthropic SDK (not LangGraph or the Agent SDK) | M8 |
| Job queue | RQ on Redis | M1, M3 |

---

## 5. Phases and milestones

| Phase | Milestones | Exit criteria (checked at the gate) |
| --- | --- | --- |
| 1 — Foundation | M1, M2, M3 | `make dev` works; every valid fixture uploads and becomes a registered Parquet dataset; `make check` is green in CI |
| 2 — Data intelligence | M4, M5, M6, M7 | `make demo-data` prints, for each fixture: the profile summary, the domain, the top 10 candidates with scores, and verdicts for every R5 example |
| 3 — Agent and dashboards | M8, M9 | With FakeLLM, an API-level test runs the whole flow: session → options → flagged request → resolve → approve → build → chat edit → undo → refresh; one live-LLM run is recorded |
| 4 — User interface | M10, M11 | The same flow works in the browser, and a Playwright smoke test covers upload → approve → dashboard renders |
| 5 — Hardening and evaluation | M12 | Workspace-isolation tests pass; the full e2e test passes in CI; the eval report is compared with the Gate 1 targets (R10) |

Build the milestones inside a phase in the order listed. Stop at every phase gate (section 1, steps 6–9).

### Phase 1 — Foundation

Milestones M1–M3: a running skeleton, the shared domain model, and ingestion of the MVP log formats.

**Exit criteria:** `make dev` works from a fresh clone; every valid fixture uploads and becomes a registered Parquet dataset; `make check` is green locally and in CI. Then stop at the phase gate.

#### M1 — Bootstrap

Goal: a working monorepo skeleton that every later milestone builds on. No domain logic yet.

Build:

- The folder layout from section 3, with empty packages.
- **CLAUDE.md** at the repo root containing: a one-paragraph description of Pheme; the lines "The build plan is PHEME_IMPLEMENTATION.md; current status is in docs/progress.md; follow section 1 of the plan. Work one phase at a time, stop at every phase gate, and never start a phase without an explicit go-ahead."; and sections 2 and 3 of this file copied verbatim.
- Backend: FastAPI app with `GET /health` that checks Postgres, Redis and MinIO; settings from env via pydantic-settings; uv project; ruff, mypy and pytest configured.
- `docker-compose.yml` with postgres, redis and minio (bucket auto-created), healthchecks, named volumes, and a complete `.env.example`.
- Alembic initialised against Postgres with an empty baseline migration.
- RQ worker entrypoint (`backend/app/jobs/worker.py`) with one no-op job and a test that runs it.
- Frontend: Vite + React + TypeScript + Tailwind with a two-pane shell (chat on the left, canvas on the right) and a `/library` route; Vitest and eslint configured.
- Makefile targets: dev, check, e2e, types, eval and demo-data (the last four can be placeholders for now).
- A GitHub Actions workflow that runs `make check` on every push.
- `docs/progress.md` with a checklist grouped by phase, with a "gate passed" line per phase:
  - Phase 1 Foundation: M1 Bootstrap, M2 Models, M3 Ingestion
  - Phase 2 Data intelligence: M4 Profiler, M5 Query, M6 Recommender, M7 Feasibility
  - Phase 3 Agent and dashboards: M8 Orchestrator, M9 Dashboards & edits
  - Phase 4 User interface: M10 UI chat & proposals, M11 UI dashboard & library
  - Phase 5 Hardening and evaluation: M12
- Empty `docs/plans/` and `docs/reports/` folders.
- README with setup in 20 lines or fewer.

Out of scope: models, parsers, LLM calls.

Done when:

- `make dev` starts everything and `/health` reports ok for all three dependencies.
- `make check` passes locally and in CI.
- A fresh clone plus `.env` copied from `.env.example` plus `make dev` works with no other steps.

#### M2 — Domain models, spec schema, database

Reference: R1, R2, R5, R6.

Goal: define the domain model every other milestone depends on.

Build in `backend/app/models/` (Pydantic v2, strict, documented):

- Dataset, DatasetVersion, ColumnProfile, DatasetProfile. Column role enum: time, measure, count, duration, ratio, category, identifier, status, text, geo, hierarchy_level.
- SemanticQuery (dataset, dimensions, measures, filters, time_grain, sort, top_n, limit) and Measure (id, plus an expr in a small whitelisted grammar: count, count_if, sum, avg, min, max, percentile, ratio).
- ChartRequest.
- VisualOption: id, title, chart_type, fields, query, encoding, rationale, score breakdown, and status (recommended | optional | requested | needs_approval | excluded).
- Verdict: feasible | adjusted | not_feasible | needs_clarification, with reasons, alternatives and applied_change.
- DraftPlan with PlanOp (add, remove, change_type, change_encoding, filter, derive_metric, layout, style, annotate), and Decision (item, reason, options, choice, decided_by, decided_at).
- DashboardSpec matching R2: datasets (mode snapshot | live), filters, measures, layout (12-column grid), widgets, annotations, thresholds and provenance. Widget types: kpi, line, area, bar, stacked_bar, donut, histogram, box, scatter, heatmap, pareto, table.
- Widget-id patch ops (paths like `/widgets/{widget_id}/type`), with functions to translate them to RFC 6902 index paths, apply them, and invert them for undo.

Also:

- SQLAlchemy tables + an Alembic migration for: workspaces, users, uploads, datasets, dataset_versions, profiles, parser_profiles, sessions, messages, draft_plans, plan_versions, decisions, dashboards, dashboard_versions, audit_events. Use JSONB for specs, plans and profiles. dashboard_versions is append-only.
- Export JSON Schemas for DashboardSpec and DraftPlan to `backend/app/models/schemas/`, and make `make types` generate TypeScript types from them into `frontend/src/api`.

Done when:

- Round-trip tests (model → JSON → model) pass for every model.
- Validation rejects an unknown widget type, a duplicate widget id, a layout item pointing to a missing widget, and a measure expr outside the grammar.
- Property test: applying a patch and then its inverse returns the original spec exactly.
- The migration applies and rolls back cleanly on an empty database.

#### M3 — Ingestion and parsers

Goal: turn uploaded files into typed, versioned datasets.

Build in `backend/app/ingestion/`:

- `POST /uploads` (multipart, streamed to MinIO, sha256 recorded, size limit from config, default 200 MB) creates an Upload and enqueues an RQ ingestion job. `GET /uploads/{id}` returns the status, any errors and the resulting dataset ids.
- Format detector: uses the extension plus the first 64 KB (XML root element, JSON vs NDJSON, CSV delimiter, JMeter JTL header) to return (format, confidence).
- Parsers: CSV/TSV, JSON, NDJSON, Excel (.xlsx), JUnit/xUnit XML (nested testsuites; passed, failed, error, skipped) and JMeter JTL (CSV and XML variants).
- Normalizers to canonical tables:
  - `test_results(run_id, build, suite, case, status, duration_ms, error_type, error_message, timestamp)`
  - `perf_samples(timestamp, label, latency_ms, success, bytes, threads, response_code)`
  - a generic typed table for everything else
  
  Durations become ms. Timestamps become UTC, with the original timezone kept in a column. Statuses are standardised (pass/ok/success → passed, and so on).
- Write each dataset to MinIO as Parquet. Register Dataset + DatasetVersion with a schema_hash (a stable hash of column names and types), the row count and lineage back to the upload.
- `POST /sessions/{id}/context` accepts free text such as "p95 must be < 800 ms, env=staging". Store it raw, and extract simple key=value and threshold facts with regex. LLM extraction arrives in M8.
- Unknown text logs return format=unknown with a readable message. Learned parsers (Drain3 + LLM) come after the MVP; leave `TODO(post-mvp)`.

Fixtures in `backend/tests/fixtures/`:

- a JUnit XML file with 3 suites and some failures across 5 builds
- a JMeter JTL CSV with about 2,000 rows
- one CSV file and one JSON file
- one malformed file per format

Done when:

- The detector picks the right format for every fixture with confidence ≥ 0.9.
- 100% of rows in the valid fixtures parse. Malformed files fail with a readable error, never a stack trace.
- A 200 MB synthetic CSV ingests without being loaded fully into memory (use Polars lazy or batched reads, and mark the test slow).
- Re-uploading a file with the same columns produces the same schema_hash.

### Phase 2 — Data intelligence

Milestones M4–M7: profiling, safe querying, chart recommendation and feasibility checks. All deterministic; no LLM calls in this phase.

**Phase deliverable:** `make demo-data` (`scripts/demo_data.py`). For each fixture it prints the profile summary, the detected domain, the top 10 candidates with score breakdowns, and the verdict plus alternative for every example request in R5.

**Exit criteria:** `make demo-data` output matches the M6 and M7 "Done when" expectations, and `make check` is green. Then stop at the phase gate.

#### M4 — Profiler and domain packs

Reference: R9.

Goal: produce the compact, factual profile the LLM reasons over, and pick a domain pack.

Build in `backend/app/profiling/` and `backend/app/domain_packs/`:

- `profile_dataset(dataset_version_id) -> DatasetProfile`, computed with DuckDB over the Parquet file:
  - Per column: semantic role, null %, distinct count, min/max/mean/p50/p95, skew and top-k values. For time columns, also span, median gap (granularity) and a monotonic flag.
  - Per dataset: row count, grain guess, candidate keys, hierarchies (e.g. suite → case), correlations between measures, outliers (IQR), trend strength per measure over time (Mann-Kendall or slope/variance) and simple change points.
- Role inference rules from name, type and values, written as data rather than if/else chains. Examples: `*_ms` or "duration" → duration; ≤ 6 distinct values in a column named status or result → status; mostly-unique strings → identifier.
- Domain classifier: rules over the canonical table kind and column names return test_execution | performance | app_logs | cicd | generic, with a confidence. Leave a hook for LLM confirmation in M8.
- Domain packs as YAML: KPIs (id, label, measure expr, format, good direction), signature visuals (chart type + field roles) and field synonyms. Write full packs for test_execution and performance, and stubs for app_logs, cicd and generic.
- Size guard: a serialised profile is ≤ 5 KB for a 50-column dataset (truncate top-k lists, round numbers).
- `GET /datasets/{id}/profile`.

Done when:

- Snapshot tests of the profile pass for every fixture.
- The JUnit fixture classifies as test_execution, the JTL fixture as performance and the generic CSV as generic.
- Profiling the 2,000-row JTL fixture takes under 1 s.
- The 50-column size-guard test passes.

#### M5 — Semantic query service

Goal: one safe path from a SemanticQuery to rows, used by the agent, the builder and the UI.

Build in `backend/app/query/`:

- `compile(SemanticQuery, dataset schema)` → DuckDB SQL over the dataset's Parquet file. It supports:
  - dimensions
  - measures from the whitelisted grammar: count, count_if, sum, avg, min, max, percentile(p), and the ratio of two measures
  - filters: eq, in, range and relative time
  - time_grain bucketing: minute, hour, day, week, plus an ordinal "build" grain
  - sort, top_n with an "Other" bucket, and limit
- A small parser for measure expressions (no eval). Anything outside the grammar is rejected with a clear message.
- `execute()`: a read-only DuckDB connection with a 30 s timeout and a row cap from config. It returns columns, rows and a query_hash.
- A result cache in Redis keyed by (query_hash, dataset_version_id).
- Advanced SQL escape hatch: validate with sqlglot (exactly one SELECT, no DDL/DML, only this dataset's table, whitelisted functions, no file or network table functions), then run it with the same limits.
- An LTTB downsampling helper for time series over 2,000 points.
- `POST /query`, scoped to the caller's workspace.

Done when:

- Golden tests pass: pass rate by build (JUnit fixture) and p95 latency per label (JTL fixture) match values computed independently in the test with Polars.
- Injection tests are rejected: stacked statements, DROP/INSERT, `read_csv('/etc/passwd')`, `read_parquet` on another path, and httpfs URLs.
- top_n plus "Other" always sums to the ungrouped total (property test).
- A cached query returns without touching DuckDB (assert with a spy).

#### M6 — Recommendation engine

Reference: R3, R4, R9. No LLM calls in this milestone; LLM curation is added in M8.

Goal: deterministic candidate generation and scoring.

Build in `backend/app/recommend/`:

- Chart grammar: one declarative rule per row of R3. Each rule emits candidate VisualOptions with a ready SemanticQuery and encoding.
- Enforce every guard rule in R3.
- Scoring exactly as in R4, with the weights in config.
- Domain-pack KPIs become KPI-tile candidates automatically.
- `recommend(dataset_ids, requests: list[ChartRequest] | None, k=25)` returns ranked candidates. Each has its per-term score breakdown and the profile facts behind it.

Done when:

- JUnit fixture: the top 10 include the pass-rate KPI, pass rate by build, failures by error type (Pareto) and duration distribution per suite.
- JTL fixture: the top 10 include p95 latency over time, a response-time histogram, throughput over time and errors by label.
- No candidate ever violates a guard rule (property test over generated profiles).
- An explicitly requested, valid chart always ranks in the top 5.
- Every candidate's query compiles with the M5 compiler.

#### M7 — Feasibility validator and decisions

Reference: R5.

Goal: decide, for every user-requested chart or edit, whether it can be built as asked, and record what the user decides.

Build in `backend/app/feasibility/`:

- Field resolver: maps the user's words to columns by exact match, then domain-pack synonyms (e.g. "response time" → latency_ms), then fuzzy match (rapidfuzz). It returns candidates with a confidence. Below the threshold it returns needs_clarification with the top 3 candidates. Keep an interface so embedding similarity can be added later.
- `check(ChartRequest, profile) -> Verdict` runs the checks in R5 in order. Each failing check gives a plain-language reason, plus at least one alternative ChartRequest when one exists.
- Adjusted verdicts carry the exact change applied (e.g. pie → top-10 bar + Other), so the UI can show before and after.
- Decisions: create one per adjusted or not_feasible verdict. API: `GET /plans/{id}/decisions`, and `POST /decisions/{id}` with a choice of accept_alternative | keep_as_asked | drop | provide_data. keep_as_asked is only allowed when the original request is buildable.
- `can_build(plan) -> (bool, open decision ids)`.

Done when:

- Table-driven tests cover every row of R5, each with its expected verdict and alternative.
- Any plan with an open decision reports can_build = false, and resolving the last one flips it to true.
- Every reason string is under 25 words and free of column-type jargon (assert against a word list), so a non-technical user can read it.

### Phase 3 — Agent and dashboards

Milestones M8–M9: the chat agent with its guarded state machine, then building, versioning, editing and refreshing dashboards.

**Exit criteria:** an API-level integration test with FakeLLM runs the whole flow on the JUnit fixture: create session → upload → options list → ask for a chart that gets flagged → resolve the decision → approve → build → edit a widget via chat → undo → upload a second file to refresh. One live-LLM run is recorded in `docs/progress.md`, and `make check` is green. Then stop at the phase gate.

#### M8 — Agent orchestrator and chat API

Reference: R1, R6, R7.

Goal: the chat agent that drives a session from upload to an approved plan.

Build in `backend/app/orchestrator/`:

- A state machine persisted per session, as in R1. Transitions and guards live in code. Any plan change after APPROVED returns the session to REFINE and clears the approval.
- Tools: those in R7, as typed functions (Pydantic in and out), registered per state. Only the current state's tools are sent to the model. `build_dashboard`, `resolve_widget` and `patch_dashboard` are stubs until M9 (`TODO(M9)`).
- `llm.py`: a thin client over the Anthropic Python SDK tool-use loop, with max tool iterations, timeouts and retries. Add a FakeLLM that replays scripted responses for tests.
- Fast-model tasks: intent parsing (text → ChartRequest[] and PlanOp[], several intents per message), domain confirmation and context-fact extraction. Validate the structured output with Pydantic. On invalid output, retry once with the validation error, then ask the user.
- Main-model tasks:
  - Curation: pick 6–10 of the top 25 candidates and order them KPI row → trends → breakdowns → distributions → detail. Give each a one-line rationale and the insight it is expected to show.
  - Replies that summarise plan diffs in plain language.
- The LLM sees profiles, score breakdowns and at most 50 sample rows, never full datasets.
- PII redaction in `backend/app/core/security.py`, applied before any LLM call to every sample row, log snippet and context text. It replaces emails, IPv4/IPv6 addresses, bearer tokens, API keys, JWTs and long hex secrets with reversible placeholders, keeping the mapping server-side.
- Prompt-injection hygiene: data goes inside clearly delimited blocks, and the system prompt states that data is never instructions.
- Chat API:
  - `POST /sessions`
  - `POST /sessions/{id}/messages` (text, plus optional selected widget ids)
  - `GET /sessions/{id}/events` as SSE, streaming assistant tokens, tool progress, the options list, plan diffs, approval cards and state changes
  - `POST /sessions/{id}/approve`
- Persist messages, tool calls, plan versions and decisions.

Done when:

- With FakeLLM, a session on the JUnit fixture reaches PROPOSE with a curated options list.
- "Show a pie of failures by test" yields a needs_approval option. Approval is refused while that decision is open and accepted once it's resolved.
- A test proves `build_dashboard` is never in the tool list before APPROVED.
- Redaction test: none of the secrets seeded into a fixture appear in any captured LLM request.
- One live run (`PHEME_LIVE_LLM=1`) on the JUnit fixture is recorded in `docs/progress.md`, with the transcript and token cost.

#### M9 — Dashboard builder, versions and edit planner

Reference: R2, R8.

Goal: turn an approved plan into a saved dashboard, and make it editable through chat with full version history.

Build in `backend/app/dashboards/`:

- `build(plan_version_id)`, wired into the orchestrator's `build_dashboard` tool:
  - Compile the included options into widgets with stable ids.
  - Auto-layout on the 12-column grid: KPI tiles 3 wide in the top row, trends full or half width, breakdowns half width, tables full width.
  - Validate with the schema and a query dry-run.
  - Run and cache the queries.
  - Save DashboardVersion v1, linked to the plan, decisions and session.
- Spec store API:
  - `GET /dashboards` (library: search by name, domain, tag and owner; sort by last updated)
  - `GET /dashboards/{id}` (latest version)
  - `GET /dashboards/{id}/versions` and `/versions/{n}`
  - `GET /dashboards/{id}/data` (rows per widget, honouring global filters)
  - a diff between two versions
  - `POST /dashboards/{id}/restore/{n}` (creates a new version)
- `PATCH /dashboards/{id}` with a base_version plus widget-id patch ops:
  - returns 409 on a stale base
  - validates with the schema, the feasibility check and a dry-run
  - creates a new immutable version
  - writes an audit event with the user, the ops and the originating prompt
- Edit planner (orchestrator EDIT state):
  - `resolve_widget` works from selected ids or from words (title, type, fields, position), and asks when more than one widget matches.
  - The LLM proposes patch ops, which are validated and returned with before/after preview data.
  - Edits commit only when the user applies them. A bulk edit ("use p95 in every latency chart") produces one version.
- Data modes and refresh: a dashboard binds in snapshot or live mode. Attaching a new upload to a live dashboard runs ingest → schema compatibility check (same hash or a superset) → re-query. On a mismatch, return a proposed field mapping instead of failing.

Done when:

- Building from the M8 test session produces a valid spec in which every widget returns data.
- One FakeLLM edit scenario per row of R8 passes, and undo restores the previous spec byte for byte.
- Of two concurrent patches on the same base_version, one succeeds and the other gets 409.
- Uploading a second JUnit file to a live dashboard adds its build to the pass-rate-by-build chart.

### Phase 4 — User interface

Milestones M10–M11: the chat and proposal experience, then the dashboard canvas, library and editing.

**Phase deliverable:** a Playwright smoke test (FakeLLM) covering upload → options appear → approve → dashboard renders, wired into `make e2e`. M12 extends it into the full end-to-end test.

**Exit criteria:** the Phase 3 flow works by hand in the browser; `make e2e` and `make check` are green. Then stop at the phase gate.

#### M10 — Frontend: upload, chat, options, approvals

Reference: R5, R6.

Goal: the left-hand experience: upload, chat, review options, resolve flags, approve.

Build:

- `src/charts`: the shared renderer, one pure function per widget type that maps (widget spec, rows) to an ECharts option. Use theme tokens for colours and status tokens (good / warning / critical) for thresholds. Give axes, number formats, empty data and long labels sensible defaults. Unit-test every mapping.
- `features/chat`:
  - a file drop zone with upload progress (calls `/uploads` and polls the status)
  - a context box for SLAs and thresholds
  - a chat panel that consumes the SSE stream, showing streaming assistant text, tool-progress lines, and quick-reply chips for clarifying questions
- `features/proposal`: one card per VisualOption with a title, chart type, fields, rationale, status badge, score breakdown on hover, an include/exclude toggle, and a live preview thumbnail drawn by `src/charts` from sample rows fetched via `/query`.
- Approval cards for open decisions, showing the reason and before/after previews, with the actions Accept alternative / Keep as asked / Drop / Provide more data.
- Plan diffs shown in the chat as a compact change list.
- An "Approve plan" button that stays disabled while any decision is open, with a tooltip naming the open items.
- Generated API types from `make types`, and TanStack Query for data fetching.

Design: calm and neutral, with generous whitespace and one accent colour. Readable at 1280 px and usable at 1024 px.

Done when:

- Vitest covers every renderer mapping and the approval-button logic.
- Against the running backend with FakeLLM, the JUnit flow works in the browser up to approval, including resolving one flagged option.
- Every action is reachable by keyboard, and every card and button has an accessible name.

#### M11 — Frontend: dashboard canvas, library, editing

Reference: R8.

Goal: view, revisit and edit dashboards.

Build in `features/dashboard` and `features/library`:

- `/d/:id` and `/d/:id/v/:n`: a react-grid-layout canvas that renders every widget with `src/charts`, plus a global filter bar and loading, empty and error states for each widget.
- Direct manipulation: dragging, resizing and inline title renaming produce widget-id patch ops, sent via PATCH with the base_version. On a 409, refetch and reapply if nothing conflicts; otherwise tell the user what changed.
- Selection: a click selects a widget (Shift-click selects several). The chat then shows a context chip ("Editing: Pass rate by build") and sends the selected ids with the next message.
- Agent edit previews: before and after side by side in the chat, with Apply and Adjust actions.
- Version history drawer: versions with author, time and change note, plus view diff and restore.
- `/library`: search, filter by domain and tag, sort by last updated, pin favourites, open.
- Refresh on live dashboards: an "Upload new data" button. If the schema changed, show the agent's proposed field mapping for the user to confirm.
- Export the current view to PNG by composing each widget's ECharts `getDataURL` onto one canvas. PDF export can wait.

Done when:

- Vitest covers patch generation for drag, resize and rename, and the 409 path.
- In the browser with FakeLLM, this flow works: build a dashboard, rename a widget inline, change a chart type via chat, undo from history, reopen the dashboard from the library and see every version listed.

### Phase 5 — Hardening and evaluation

Milestone M12: auth and isolation, audit, observability, limits, the evaluation harness and the full end-to-end test.

**Exit criteria:** workspace-isolation tests pass; the full e2e test passes in CI; `make eval` has produced a report, and its numbers are compared with the Gate 1 targets in R10. Then stop at the final gate: summarise MVP readiness and list what you recommend for the next release.

#### M12 — Security, observability, evaluation, end-to-end test

Reference: R10.

Goal: make the MVP safe to pilot and measurable.

Build:

- Auth: the simplest option that can grow into SSO later, e.g. OIDC via an off-the-shelf library, with email login for local dev. Every endpoint is scoped to the workspace. Workspace-owned tables use Postgres row-level security. Dashboards get owner / editor / viewer roles and a share endpoint.
- Audit: every tool call, approval, decision and patch is written to audit_events; add `GET /dashboards/{id}/audit`.
- Observability:
  - OpenTelemetry traces across API → orchestrator → tools → Postgres/DuckDB
  - LLM spans recording the model, input/output tokens, latency and estimated cost
  - structured JSON logs
  - an optional Langfuse exporter behind an env flag
- Limits: per-workspace rate limits and upload size limits.
- Evaluation harness in `backend/eval/`:
  - A golden-set folder format: the log file, the expected domain, the expert top-8 options, labelled infeasible requests with their expected verdicts, and edit scenarios with their expected spec diffs.
  - A runner that reports every metric in R10.
  - Seed it with the fixtures. Real-LLM runs happen only with `PHEME_LIVE_LLM=1`. Write results to `backend/eval/reports/<date>.md`.
- Playwright e2e test (FakeLLM, runs in CI): upload JUnit → options appear → ask for an infeasible chart → resolve the flag → approve → dashboard renders → edit via chat → undo → reopen from the library.

Done when:

- A user in workspace A can't read or change anything in workspace B, with tests at both the API and database level.
- `make e2e` passes in CI.
- `make eval` produces a report. The first numbers are recorded in `docs/progress.md` and compared with the Gate 1 targets in R10.

---

## 6. Reference

### R1 — Session state machine

```
INGEST → PROFILE → PROPOSE → REFINE (one loop per chat turn)
REFINE → [gate: user approves AND zero open decisions] → APPROVED → BUILD → PUBLISHED
gate fails → back to REFINE
PUBLISHED → EDIT (resolve widget → patch → validate → preview → commit vN+1) → PUBLISHED
PUBLISHED → REFRESH (new upload, same spec, re-query) → PUBLISHED
```

Guards, enforced in code:

- Each state exposes only its own tools to the LLM (see R7).
- APPROVED requires zero open decisions and an explicit approve event (a button, or a chat message the agent confirms).
- Any plan change after approval returns to REFINE, and the user must approve again.
- `patch_dashboard` requires base_version == the current version.
- Ingest and build run as async jobs that stream progress. State is persisted so a session can resume on any device.

### R2 — Dashboard Spec example

```json
{
  "dashboard_id": "dsh_7f3a",
  "version": 3,
  "title": "Regression run — Sprint 42",
  "datasets": [{"id": "ds_tests", "kind": "test_results", "schema_hash": "a91c…", "mode": "live"}],
  "filters": [{"id": "f_env", "field": "environment", "control": "multiselect"}],
  "measures": [{"id": "pass_rate", "expr": "count_if(status = 'passed') / count()", "format": "percent"}],
  "layout": {"columns": 12, "items": [
    {"widget": "w_pass_kpi",   "x": 0, "y": 0, "w": 3, "h": 2},
    {"widget": "w_pass_trend", "x": 3, "y": 0, "w": 9, "h": 4}
  ]},
  "widgets": [
    {"id": "w_pass_kpi", "type": "kpi", "title": "Pass rate",
     "query": {"dataset": "ds_tests", "measures": ["pass_rate"]},
     "target": 0.95, "thresholds": [{"below": 0.90, "color": "critical"}]},
    {"id": "w_pass_trend", "type": "line", "title": "Pass rate by build",
     "query": {"dataset": "ds_tests", "dimensions": ["build_no"], "measures": ["pass_rate"], "sort": "build_no"},
     "encoding": {"x": "build_no", "y": "pass_rate"},
     "annotations": [{"type": "rule", "y": 0.95, "label": "Target"}],
     "provenance": {"option_id": "O2", "rationale": "Trend across 14 builds"}}
  ]
}
```

### R3 — Chart grammar (candidate rules)

| Data pattern | Candidate charts | Guard rule |
| --- | --- | --- |
| One measure, no dimension | KPI tile, gauge, bullet chart | Gauge or bullet only when a target is known |
| Time + measure | Line, area, column, sparkline | At least 3 time points; column only up to ~30 periods |
| Time + measure + category | Multi-line, stacked area, small multiples | More than 7 series → top N + "Other", or small multiples |
| Category + measure | Sorted bar, lollipop, Pareto | More than 20 categories → top N |
| Part of a whole | 100% stacked bar, donut, treemap | Donut only up to 6 parts |
| One continuous measure | Histogram, box/violin, ECDF | At least 30 rows |
| Measure spread by category | Box plot, strip plot | At least 5 rows per category |
| Two measures | Scatter (optionally coloured by category), hexbin | Above ~10k points → hexbin or sample |
| Two categories + measure | Heatmap, grouped bar | Grouped bar only for small × small |
| Events with start and end | Gantt / timeline | Needs both timestamps |
| Source → target flows | Sankey | Needs paired fields |
| Hierarchy + measure | Treemap, sunburst | Needs a detected hierarchy |
| Row-level detail | Sortable table with conditional formatting | Always available |

### R4 — Scoring

`S = 0.30·DataFit + 0.25·Intent + 0.20·Insight + 0.15·Perception + 0.10·DomainPrior − Redundancy − Clutter` (weights in config)

- **DataFit:** how well the field roles, cardinality and row count fit the chart's grammar.
- **Intent:** match to what the user asked for. An explicitly requested chart scores 1.0.
- **Insight:** evidence in the profile that the chart will show something (trend strength, spread across categories, outliers, change points). A flat line scores low.
- **Perception:** encoding effectiveness, following the Cleveland–McGill ranking (position > length > angle > area > colour).
- **DomainPrior:** a boost for the active domain pack's signature visuals.
- **Redundancy:** an MMR-style penalty when a higher-ranked chart already shows the same measure × dimensions.
- **Clutter:** a penalty for too many series, labels or points.

### R5 — Feasibility checks

Checks run in this order. Verdicts: feasible (added as is), adjusted (added; the change needs approval), not_feasible (listed with the reason and alternatives), needs_clarification (the agent asks).

| Check | Example request | Verdict | Agent offers |
| --- | --- | --- | --- |
| Field exists | "CPU usage over time", but there's no CPU column | not_feasible | Drop it, or upload infrastructure metrics |
| Type fit | "Line chart of test names" | not_feasible | Bar chart of runs per test |
| Granularity | "Hourly trend", but timestamps are daily | adjusted | Daily trend |
| Cardinality | "Pie of failures by test", with 240 tests | adjusted | Top-10 bar + "Other" |
| Volume | Scatter of 8 M samples | adjusted | Hexbin, or a 50k-row sample |
| Derivation | "MTTR", but there's no resolution time | not_feasible | Explain which field would be needed |
| Renderer support | "3D globe of traffic" | not_feasible | Map or bar chart by region |
| Ambiguity | "Show performance" | needs_clarification | Ask: latency, throughput or errors? |

Decision choices: accept_alternative, keep_as_asked (only if the original is buildable), drop, provide_data.

### R6 — Plan intents (chat refinement)

| Intent | Example message | Effect on the plan |
| --- | --- | --- |
| add_widget | "Add a histogram of test durations" | New option; feasibility check runs |
| remove_widget | "Drop the donut" | Option set to excluded |
| change_type | "Make the trend a bar chart" | Chart type changed; guard rules re-checked |
| change_encoding | "Split by environment", "use p95, not average" | Dimension or aggregation changed |
| filter | "Only the last 7 builds", "exclude the smoke suite" | Widget or global filter added |
| derive_metric | "Show pass rate as a percentage" | Derived measure added and validated |
| layout | "KPIs on top, two columns below" | Layout hints updated |
| style | "Red when below 90%" | Threshold rule added |
| annotate | "Mark the 3.2 release" | Annotation added |
| explain | "Why a Pareto here?" | No change; answered from the score breakdown |
| approve / undo | "Looks good, build it", "go back one step" | Approval request, or plan version reverted |

Turn handling: one message may hold several intents, which are applied together. Feasibility re-runs only for changed options. The reply is a short diff (e.g. "Added O7, changed O2 to p95, 1 item needs approval") with refreshed previews. If a message is ambiguous, ask one question and offer quick-reply chips.

### R7 — Agent tools

| Tool | Purpose | Offered in states |
| --- | --- | --- |
| `ingest_files(file_ids)` | Start ingestion; returns job and dataset ids | INGEST, REFRESH |
| `infer_parser(file_id)` | Draft a parser for an unknown log (post-MVP) | INGEST |
| `confirm_parser(profile_id, mapping)` | Save the user-confirmed field mapping (post-MVP) | INGEST |
| `profile_dataset(dataset_id)` | Return the profile JSON | PROFILE onward |
| `query(semantic_query, limit)` | Rows (max 500) for the agent's own reasoning | All |
| `recommend_visuals(dataset_ids, intent)` | Ranked candidates with score breakdown | PROPOSE, REFINE |
| `parse_chart_request(text)` | Text → ChartRequest[] | PROPOSE, REFINE, EDIT |
| `check_feasibility(request)` | Verdict, reasons, alternatives | PROPOSE, REFINE, EDIT |
| `update_plan(ops)` | Apply plan operations; returns a diff | PROPOSE, REFINE |
| `render_preview(widget_spec)` | Sample rows for a thumbnail | PROPOSE, REFINE, EDIT |
| `request_approval(items)` | Show approval cards in the UI | REFINE, EDIT |
| `build_dashboard(plan_id)` | Compile and save the dashboard; returns id and URL | APPROVED only |
| `get_dashboard(id, version)` | Read a spec | EDIT, PUBLISHED |
| `resolve_widget(dashboard_id, reference)` | Words or selection → widget ids | EDIT |
| `patch_dashboard(id, base_version, ops)` | Validate and commit a patch | EDIT |
| `revert_dashboard(id, version)` | Restore a version | EDIT |
| `list_dashboards(filter)` | Library search | All |

### R8 — Edit scopes

| Scope | Example edits |
| --- | --- |
| Widget data | Change the measure or aggregation, add a split, filter, set top N |
| Widget visuals | Chart type, colours, thresholds, log scale, labels, sort order |
| Widget content | Title, description, annotations, reference lines |
| Layout | Move, resize, reorder, group into sections or tabs |
| Dashboard | Add or remove widgets, global filters, theme, title, pages |
| Bulk | "Use p95 in every latency chart", "rename env to Environment everywhere" |

### R9 — Domain packs

| Domain | KPIs | Signature visuals |
| --- | --- | --- |
| test_execution | Pass rate, failed count, flaky rate, total and average duration | Status breakdown, pass-rate trend by build, slowest tests (top N), failure-reason Pareto, flaky-test heatmap (test × run) |
| performance | Throughput, p50/p90/p95/p99 latency, error rate, Apdex | Percentile lines over time, response-time histogram, throughput vs users, errors by endpoint, SLA breach table |
| app_logs (stub) | Error rate, events per minute, top error signatures | Log-level stacked area, error-signature Pareto, hour × weekday heatmap, incident timeline |
| cicd (stub) | Build success rate, lead time, stage duration, queue time | Stage-duration Gantt, success trend, duration box plot per stage |
| generic (stub) | Derived from the profile | Distribution, comparison, correlation and composition sets |

### R10 — Evaluation targets

| Metric | How it's measured | Target |
| --- | --- | --- |
| Parse coverage | Share of lines parsed | ≥ 98% known formats (≥ 90% for learned parsers, post-MVP) |
| Recommendation quality | Overlap of the top 8 picks with the expert set | ≥ 70% |
| Spec validity | Share of generated specs that validate and render | ≥ 99% |
| Feasibility accuracy | Precision / recall on labelled "can't build" requests | ≥ 95% / ≥ 90% |
| Edit success | Edits applied correctly on the first try (human-rated) | ≥ 90% |
| Time to first proposal | Upload (≤ 50 MB) to options list | < 30 s |
| Dashboard reopen | Render from cache | < 2 s |

Gate 1 (end of the MVP, checked at the Phase 5 gate): pilot datasets produce valid dashboards, and feasibility precision is ≥ 95%.

---

## 7. Prompts for the human

Paste these into Claude Code as needed. Replace the `<...>` placeholders first. A fresh session per phase keeps the context clean, because `docs/progress.md` and the phase reports carry the state between sessions.

**Start Phase 1 (first session)**

```text
Read PHEME_IMPLEMENTATION.md and follow section 1, "How to work". Start Phase 1.
```

**Proceed after a gate (same session)**

```text
Proceed to Phase <N+1>.
```

**Proceed after a gate (fresh session)**

```text
Read CLAUDE.md, PHEME_IMPLEMENTATION.md, docs/progress.md and docs/reports/phase-<N>.md. Phase <N> is approved. Proceed to Phase <N+1>, following section 1.
```

**Plan a phase without building it**

```text
Read PHEME_IMPLEMENTATION.md and follow section 1 for Phase <N>, plan only: write docs/plans/phase-<N>.md, then stop for my review.
```

**Approve a reviewed plan**

```text
Plan approved. Build Phase <N> and stop at the phase gate.
```

**Ask for changes at a gate**

```text
Before moving on, change <what> in Phase <N> because <why>. Then re-run the phase gate (section 1, steps 6–8) and stop again.
```

**Resume a phase interrupted mid-way**

```text
Read CLAUDE.md, PHEME_IMPLEMENTATION.md, docs/progress.md and docs/plans/phase-<N>.md. Summarise where Phase <N> stands, milestone by milestone, against the "Done when" lists. Then continue with the next unfinished milestone and stop at the phase gate. Don't redo finished work.
```

**Review before merging**

```text
Review the changes on this branch against section 2 of PHEME_IMPLEMENTATION.md and the "Done when" lists and exit criteria of Phase <N>. First report any rule violations: the LLM seeing raw data or unredacted rows, SQL or rendering code in a spec, tools exposed in the wrong state, a build path that skips the approval gate, missing tests. Then list smaller issues by severity. Don't change code yet.
```

**Fix failing checks**

```text
Run make check. For each failure, find the root cause and fix it without weakening, skipping or deleting tests. Rerun until everything is green, then summarise what was wrong and what you changed.
```

**Change a decision**

```text
We're changing <decision> from <old> to <new> because <reason>. First list every module, test and doc this touches, and put a migration plan in docs/plans/. After I approve, update PHEME_IMPLEMENTATION.md (section 4), CLAUDE.md and the code, keep make check green, and record the decision in docs/progress.md.
```

**Add a new log format (after the MVP)**

```text
Add support for <format, e.g. Gatling simulation.log>. Add a detector signature, a parser that normalises into the right canonical table, a fixture plus a malformed fixture, and tests that meet the M3 "Done when" bar. If it belongs to a new domain, add or extend the domain-pack YAML (R9).
```
