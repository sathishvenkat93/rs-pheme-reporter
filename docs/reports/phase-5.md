# Phase 5 report — Hardening and evaluation (final gate)

Branch `phase-5` (stacked on phases 1–4; none merged, because the GitHub push is still blocked). Commits: `be8d762` (M12), `f381939` (M13), `7c922fe` (M14).

**Status:** built and verified locally. CI and the live-LLM run are still pending; both need something from you (see the end of this report).

## What was built

**M12 — Security and audit**

- **Auth:** three modes behind one interface.
  - `none`: a fixed dev user; refused in production.
  - `dev`: email login issuing signed tokens (bearer or cookie).
  - `oidc`: bearer JWTs verified against the issuer's JWKS, checking issuer, audience and expiry. This is the route to SSO.
- **A sign-in dialog** in the UI opens on any 401.
- **Workspace isolation in the database:**
  - Row-level security on all 16 workspace-owned tables.
  - The app connects as a non-owner `pheme_app` role, and each transaction is scoped to the caller's workspace.
  - Trusted system work (the ingestion worker) uses an explicit bypass.
  - The entire test suite now runs under RLS.
- **Dashboard roles:** owner, editor and viewer. Workspace members can view; shares grant editing. Sharing and soft-delete are owner-only.
- **Audit trail:** every tool call, approval, decision, patch, restore, share and delete is recorded; `GET /dashboards/{id}/audit` returns it.

**M13 — Observability and limits**

- **OpenTelemetry tracing:** HTTP → `agent.turn` → `tool.<name>` → `duckdb.query`, plus Postgres spans and `llm.<task>` spans with model, tokens, latency and estimated cost.
- **Exporters:** none, console or OTLP; Langfuse through its OTLP endpoint.
- **JSON logs** carrying trace and span ids.
- **Per-workspace limits:** a request rate limit (429 with `Retry-After`) and an upload-size limit.

**M14 — Evaluation and end-to-end test**

- **Golden-set format** and three seed goldens (JUnit, JMeter, daily CSV).
- **`make eval`** measures every R10 metric and writes a dated report, a JSON copy and a human rating sheet for edits.
- **Playwright full flow:** upload → options → infeasible request → resolve → approve → render → chat edit → undo → reopen from library.

## Verification

| Item | How | Result |
| --- | --- | --- |
| M12: workspace A can't read or change B (API and DB) | `tests/security`: 14 read and 9 write endpoints return 404 across workspaces. As `pheme_app`, B's rows are invisible, cross-workspace inserts are refused by RLS, and the role can't disable RLS. | ✅ |
| M12: viewers can't patch; editors can't share or delete | `test_dashboard_roles_viewer_editor_owner` (403s; owner delete → 404 and gone from the library) | ✅ |
| M13: one traced request spans API → agent → tool → DuckDB, with LLM tokens and cost | In-memory exporter: span parentage and one trace id; LLM span cost = (1200·$2 + 80·$10)/1M | ✅ |
| M13: rate limit → 429 with Retry-After | `test_rate_limit_returns_429_with_retry_after` | ✅ |
| M14: `make e2e` passes | 7/7 locally, including `full.spec.ts`; CI step added | ✅ locally; CI ⏳ |
| M14: `make eval` produces a report; first numbers recorded and compared with Gate 1 | `backend/eval/reports/2026-11-09.md`; table in `docs/progress.md` | ✅ |
| Exit: `make check` green | 333 backend + 45 frontend tests, lint and types | ✅ locally; CI ⏳ |

## Evaluation (offline, seed goldens)

| Metric | Value | Target |
| --- | --- | --- |
| Parse coverage | 100% | ≥ 98% ✅ |
| Recommendation overlap | 62.5% | ≥ 46% (experts agreed 56%; the plan's relative rule) ✅ |
| Spec validity | 100% | ≥ 99% ✅ |
| Feasibility precision / recall | 100% / 90.9% | ≥ 95% / ≥ 90% ✅ |
| Edit success (automated proxy) | 100% | ≥ 90% (human rating pending) |
| Time to first proposal (50 MB) | 0.67 s | < 30 s ✅ (no upload or live-LLM latency) |
| Dashboard reopen from cache | < 10 ms | < 2 s ✅ |

**Gate 1 is met only on synthetic data with self-made expert lists**, so it isn't yet evidence for pilot data. Weaknesses the eval found, reported rather than tuned away:

- "churn rate" (absent from the data) gets a clarifying question instead of "can't build". The fuzzy matcher's middle band is too generous.
- The offline curation lets KPI tiles crowd out charts for JMeter, so the SRE expert list matched only 38%.

## Deviations (details in `docs/plans/phase-5.md`)

1. **Postgres spans come from our own SQLAlchemy event hooks.** The official OpenTelemetry instrumentation doesn't support SQLAlchemy 2.1 and silently instruments nothing.
2. **Dev login accepts any `name@domain.tld`**, because special-use dev domains like `.test` fail strict email validation.
3. **Workspace members are viewers by default**; editing needs a share.
4. **The seed goldens' expert lists were written by me** (labelled as such everywhere).
5. **Edit success is an automated proxy** until humans fill in the rating sheet.
6. **The rate limit is off in the test and e2e harnesses** (one workspace drives everything); its own test enables it.

## MVP readiness

**Ready:**

- The full product loop works end to end in a browser: upload logs, get proposals, flag and resolve unbuildable requests, approve, build a versioned dashboard, edit it by chat or directly, undo, refresh with new data, and reopen from the library.
- The design rules are enforced in code and covered by tests:
  - the LLM sees only profiles and redacted samples
  - specs contain no SQL
  - the approval gate can't be bypassed
  - per-state tools
  - workspace isolation in the database
  - an audit trail
- Observability, limits and an evaluation harness are in place.

**Not ready yet** (in order of risk):

1. **Real-model behaviour is unmeasured.** Every flow ran on the offline FakeLLM. The Anthropic client follows the SDK documentation but has never made a call: tool use, structured-output schema acceptance, curation quality and cost are all unknown.
2. **No CI run.** Everything is green locally only.
3. **No pilot evaluation.** Recommendation quality and feasibility accuracy are measured only on synthetic data with self-made labels.
4. **Production configuration isn't exercised:** OIDC against a real provider, secret management, the `pheme_app` password, TLS, backups and migration rollout.

My recommendation: a **closed pilot** once items 1–3 are done, on the supported formats (JUnit, JMeter, CSV/JSON/Excel).

## Recommendations for the next release

1. **Live-LLM hardening:** record the live run, tune the prompts and tool descriptions on real transcripts, add `PHEME_LIVE_LLM=1 make eval` to a nightly job, and stream tokens for real.
2. **Pilot evaluation:** two or more independent expert top-8 lists per pilot dataset, labelled infeasible requests from real users, and human ratings of edits. Then tune `PHEME_RECOMMEND_WEIGHTS` and the curation prompt against held-out goldens.
3. **Feasibility accuracy:** tighten the fuzzy middle band (the "churn rate" case), and add embedding similarity behind the resolver's matcher interface.
4. **Unstructured text logs:** a Drain3 template parser plus a user-confirmed field mapping (the post-MVP `infer_parser`/`confirm_parser` tools).
5. **Multi-dataset sessions and dashboards**, such as joining test results with performance runs.
6. **Accessibility and responsive layout:** an automated axe/contrast check in Playwright, and a dashboard layout for narrower screens.
7. **Operations:**
   - backups and restore drills
   - a migration runbook (role password, RLS)
   - Redis persistence
   - object-store lifecycle rules for orphaned uploads
   - a deletion policy that works with the append-only versions
8. **Excel and JSON streaming** for files over 200 MB, and avoiding the double disk write on large uploads.

## Decisions for you

1. **Unblock GitHub:** `gh auth refresh -h github.com -s workflow`, or load an SSH key. One push then sends all five stacked branches so CI can run and the PRs can open (merge in order: phase-1 → phase-5).
2. **API key:** add `PHEME_ANTHROPIC_API_KEY` to `.env` for the live run and a live `make eval`.
3. **Pilot data and independent expert labels**, for a real Gate 1.
4. **Local `.env`:** update yours from `.env.example`. The new `PHEME_DATABASE_URL` uses the `pheme_app` role; the old URL connects as the owner, which bypasses RLS.
