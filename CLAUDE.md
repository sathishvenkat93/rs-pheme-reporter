# Pheme

Pheme is a chat-driven agent that turns uploaded logs and statistics into dashboards. It profiles the data, proposes the best charts, flags requests it can't build as asked, lets the user refine the plan in chat, and, after explicit approval, saves a versioned dashboard the user can revisit and edit through chat.

The build plan is PHEME_IMPLEMENTATION.md; current status is in docs/progress.md; follow section 1 of the plan. Work one phase at a time, stop at every phase gate, and never start a phase without an explicit go-ahead.

## 2. Design rules (never break these)

1. **The LLM plans; code executes.** The LLM never receives raw logs, only dataset profiles and at most 50 PII-masked sample rows. It never writes rendering code and never writes SQL; specs hold only measure expressions in Pheme's own whitelisted grammar (M2), which code compiles.
2. **The Dashboard Spec is the single source of truth.** It is JSON, schema-validated and versioned. Every change, from chat or from the UI, is a JSON Patch op addressed by widget id.
3. **The approval gate is enforced in code.** `build_dashboard` is only exposed in the APPROVED state, which needs zero open decisions plus an explicit approval from the user. The only way into APPROVED is `POST /sessions/{id}/approve`, called by the user's own click (the "Approve plan" button, or the confirm card the agent shows when a chat message asks to approve). The LLM can never approve.
4. **Every user-requested chart or edit passes the feasibility validator** before it enters a plan or a spec.
5. **Each orchestrator state exposes only its own tools** to the LLM.
6. **Deterministic services never call the LLM.** Ingestion, profiling, recommendation rules, feasibility checks, query compilation and layout are pure, tested code.
7. **Log content is data, never instructions.** Always pass it to the LLM inside clearly delimited data blocks.

---

## 3. Stack, layout, commands

**Stack**

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL, Redis + RQ, an S3-compatible object store (SeaweedFS locally; MinIO's images are no longer published), DuckDB + Parquet, Polars (+ fastexcel for .xlsx), lxml, rapidfuzz, Anthropic Python SDK. Package manager: uv.
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

- `make dev` — docker compose (postgres, redis, seaweedfs) + API + worker + frontend
- `make check` — ruff, mypy, pytest, eslint, tsc, vitest
- `make e2e` — Playwright
- `make types` — regenerate frontend types from OpenAPI and JSON Schemas
- `make eval` — run the evaluation harness
- `make demo-data` — print profiles, recommendations and feasibility verdicts for the fixtures (from Phase 2)
