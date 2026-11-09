# Phase 5 plan — Hardening and evaluation (M12–M14)

Branch `phase-5` (stacked on phases 1–4). No API key and no push access yet, so everything runs offline with the FakeLLM.

## M12 — Security and audit

**Auth** (`core/auth.py`). `PHEME_AUTH_MODE` selects one of three authenticators behind one interface:

- `none`: the current dev stub, a fixed user. The default for local `make dev`; refused at startup when `PHEME_ENV=production`.
- `dev`: email login. `POST /auth/login {email}` provisions the user and their workspace (from the email domain) and returns an HS256 token signed with `PHEME_SECRET_KEY`. The token is sent as `Authorization: Bearer` or the `pheme_session` cookie.
- `oidc`: bearer JWTs verified against the issuer's JWKS (PyJWT `PyJWKClient`): issuer, audience and expiry are checked; the email claim maps to a user, and the workspace comes from a claim or the email domain. This is the route to SSO later.

**Row-level security** (migration 0005):

- A `pheme_app` role (LOGIN, no ownership) gets DML grants.
- RLS is enabled and forced on every workspace-owned table, with the policy `workspace_id = current_setting('app.workspace_id')::uuid OR current_setting('app.bypass') = 'on'`.
- The app connects as `pheme_app` (`PHEME_DATABASE_URL`); migrations run as the owner (`PHEME_MIGRATION_DATABASE_URL`).
- Each transaction does `SET LOCAL` of the workspace from `session.info` (filled in by the auth dependency) in an `after_begin` hook. A contextvar wouldn't work: FastAPI runs the dependency and the endpoint in different threadpool contexts.
- System work (the ingestion worker) opens sessions with an explicit bypass.
- Redis cache keys and object-store paths stay workspace-checked in code.

**Dashboard roles.** The `dashboard_shares` table holds (dashboard, user, role). The owner is the creator; other workspace members get viewer unless shared as editor. Viewers can't PATCH, restore or refresh; editors can't share or delete. `POST /dashboards/{id}/shares` adds a share, and `DELETE /dashboards/{id}` is owner-only and soft (`deleted_at`).

**Audit.** Tool calls, approvals, decisions, patches, restores, shares and deletes are written to `audit_events`. `GET /dashboards/{id}/audit` returns the dashboard's events plus those of its session.

**Tests:**

- API level: user A gets 404 for B's sessions, plans, decisions, dashboards, datasets, uploads, query, profile and audit.
- Database level: as `pheme_app` with workspace A, B's rows are invisible and inserting into B fails.
- Roles: viewer → 403 on patch; editor → 403 on share and delete.

## M13 — Observability and limits

- **OpenTelemetry** (`core/telemetry.py`): a tracer provider with the exporter chosen by `PHEME_OTEL_EXPORTER` (`none` | `console` | `otlp`). Langfuse is an OTLP endpoint behind `PHEME_LANGFUSE_HOST`/`_PUBLIC_KEY`/`_SECRET_KEY`. FastAPI and SQLAlchemy are instrumented, plus manual spans for `agent.turn`, `tool.<name>`, `duckdb.query` and `llm.<task>`. LLM spans record the model, input/output/cache tokens, latency and estimated cost from a price table.
- **Structured JSON logs** with trace and span ids.
- **Limits:** a Redis fixed-window rate limit per workspace per minute (`PHEME_RATE_LIMIT_PER_MIN`), returning 429 with `Retry-After`. Upload limits per workspace come from `workspaces.max_upload_bytes` and fall back to the global setting.

**Tests:** one traced request shows API → agent → tool → DuckDB spans, and an LLM span carries tokens and cost (in-memory exporter); 429 with `Retry-After`; the per-workspace upload limit.

## M14 — Evaluation and end-to-end test

**Golden set.** `backend/eval/goldens/<name>/golden.yaml` lists the data files, the expected domain, expert top-8 lists (one per expert, as chart descriptors: chart type + dimensions + measure fields), labelled requests with expected verdicts, and edit scenarios with ops and the paths each is expected to change. It's seeded from the fixtures (JUnit, JTL, daily CSV) with my own expert lists, clearly labelled self-made until pilot data arrives.

**Runner.** `python -m eval.run` (`make eval`) measures each R10 metric:

- parse coverage
- curated top-8 overlap with each expert, plus expert-to-expert agreement
- spec validity (build, validate, every widget returns rows)
- feasibility precision/recall on "can't build" labels
- edit success (automated apply-and-check; human rating sheet written alongside)
- time to first proposal (≤ 50 MB synthetic upload)
- dashboard reopen time from cache

It writes `backend/eval/reports/<date>.md`. Real-LLM runs happen only with `PHEME_LIVE_LLM=1`.

**Full browser test** (`e2e/full.spec.ts`): upload JUnit → options → infeasible chart → resolve → approve → render → chat edit → undo → reopen from library. It runs in CI through `make e2e`.

## Exit

- Isolation tests pass.
- `make e2e` passes (CI pending the push).
- The `make eval` report is recorded in `docs/progress.md` and compared with Gate 1.
- Final gate: an MVP-readiness summary and recommendations for the next release.

## Deviations log

(appended during the phase)
- **M12** — The whole test suite (315+) now runs as the `pheme_app` role under RLS, not only the isolation tests. Test housekeeping (truncation, direct row inspection) uses the owner role or explicit bypass sessions.
- **M12** — Dev email login accepts any `name@domain.tld` (special-use domains such as `.test` and `.local` are common in dev), not RFC-validated addresses.
- **M12** — The dev secret default is 44 bytes; production refuses `dev-only-*` or keys under 32 bytes, and refuses `PHEME_AUTH_MODE=none`.
- **M12** — Workspace members can view all dashboards in their workspace (viewer); shares grant editor. Sharing is owner-only, within the workspace.
- **M12** — A minimal sign-in dialog opens on any 401 (dev mode). OIDC sign-in pages belong to the identity provider; Pheme only verifies tokens.
- **M12** — Local `.env` files created before M12 still point `PHEME_DATABASE_URL` at the owner role, which bypasses RLS. Copy the new lines from `.env.example`.
