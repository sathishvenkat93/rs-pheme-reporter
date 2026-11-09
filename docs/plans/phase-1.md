# Phase 1 plan — Foundation (M1, M2, M3)

Branch: `phase-1`. One commit per milestone, pushed so CI runs; PR to `main` at the gate.

## M1 — Bootstrap

Files:

- `backend/pyproject.toml` (uv, Python 3.12), `backend/uv.lock`
- `backend/app/main.py` (FastAPI app factory), `backend/app/api/health.py`
- `backend/app/core/config.py` (pydantic-settings, `PHEME_` prefix), `core/db.py` (SQLAlchemy engine/session), `core/storage.py` (S3 client), `core/redis.py`, `core/principal.py` (`current_principal` dev stub), `core/logging.py`, `core/security.py` (placeholder for M8)
- Empty packages: `api, core, models, db, ingestion, profiling, domain_packs, query, recommend, feasibility, orchestrator, dashboards, jobs`
- `backend/alembic.ini`, `backend/app/db/migrations/` with an empty baseline revision
- `backend/app/jobs/worker.py` (RQ worker entrypoint), `jobs/tasks.py` (`noop` job)
- `docker-compose.yml` (postgres 17, redis 7, seaweedfs + a one-shot `s3-init` that creates the bucket idempotently), healthchecks, named volumes
- `.env.example`, `.gitignore`, `Makefile`, `scripts/dev.sh` (runs deps, API, worker and Vite together; Ctrl-C stops all)
- `frontend/` (Vite + React + TS + Tailwind v4, react-router): two-pane shell at `/`, `/library` route; Vitest + Testing Library; eslint
- `.github/workflows/ci.yml`
- `CLAUDE.md`, `README.md`, `docs/progress.md`, `docs/plans/`, `docs/reports/`

Interfaces:

- `GET /health` → `{"status": "ok"|"degraded", "checks": {"postgres": ..., "redis": ..., "object_store": ...}}`; 503 when any check fails.
- `current_principal() -> Principal(user_id, workspace_id)` from `PHEME_DEV_USER_ID` / `PHEME_DEV_WORKSPACE_ID`.

Tests: health returns ok with live deps and 503 with a broken dependency (overridden settings); settings load from env; `noop` job runs through a burst RQ worker against real Redis; frontend shell renders both panes and the library route.

Make targets: `dev`, `deps-up`, `check` (brings deps up, then ruff, mypy, pytest `-m "not slow"`, eslint, tsc, vitest), `test-slow`, `e2e`/`types`/`eval`/`demo-data` placeholders, `migrate`.

Risks:

- **Object store:** MinIO's images are no longer published; replaced with SeaweedFS (user decision). The `minio` Python client is kept, since it speaks plain S3.
- **CI services:** GitHub service containers can't pass the store's `server` command. CI therefore starts the same `docker-compose.yml` with `docker compose up -d --wait`. That's a small deviation from the plan's wording (same services, different mechanism); recorded in the report.
- Local runtime is Colima (Docker Desktop not installed).

## M2 — Domain models, spec schema, database

Files in `backend/app/models/`:

- `common.py` (strict base model: `extra="forbid"`, frozen where sensible)
- `dataset.py`: `ColumnRole`, `ColumnSchema`, `Dataset`, `DatasetVersion` (parts, schema_hash, columns), `ColumnProfile`, `DatasetProfile`
- `grammar.py`: tokenizer + recursive-descent parser for the measure grammar → AST (`Agg`, `Ratio`, `Rate`, `Named`, predicate nodes); `parse_measure(expr)` raises `MeasureSyntaxError` with a readable message. No eval, no SQL.
- `query.py`: `TimeGrain`, filter union (`eq`, `in`, `range`, `relative_time`, `last_n`) discriminated by `op`, `SortKey`, `Measure` (expr validated by the grammar), `SemanticQuery` (measures are ids into a measure catalog)
- `chart.py`: `FieldPhrase`, `ChartRequest`, `ScoreBreakdown`, `OptionStatus`, `VisualOption`
- `verdict.py`: `Verdict`
- `plan.py`: `PlanOp` (discriminated by `op`: add, remove, change_type, change_encoding, filter, derive_metric, layout, style, annotate), `DraftPlan`, `Decision`
- `spec.py`: `WidgetType`, `DatasetBinding`, `GlobalFilter`, `Threshold`, `Annotation`, `Provenance`, `LayoutItem`, `Layout`, `Widget`, `DashboardSpec` with validators: unique widget ids; layout items reference existing widgets and fit the 12-column grid; every widget measure id is defined in `spec.measures`; every widget dataset is bound; widget type is in the enum.
- `patch.py`: `WidgetPatchOp` (add | remove | replace on widget-id paths: `/title`, `/widgets/{id}[/...]`, `/layout/items/{widget_id}[/...]`, `/filters/{id}[/...]`, `/measures/{id}[/...]`); `to_rfc6902(spec, op)`; `apply_ops(spec, ops) -> (new_spec, rfc_ops, inverse_rfc_ops)`; `apply_rfc(spec, rfc_ops)`. Inverse ops are index-addressed RFC 6902 so removed items return to their original position.
- `export_schemas.py`: writes `schemas/dashboard_spec.schema.json` and `schemas/draft_plan.schema.json`.

Database: `backend/app/db/tables.py` (all 15 tables, JSONB for specs/plans/profiles, `workspace_id` on every workspace-owned table, unique `(dashboard_id, version)`, `dashboard_versions` append-only enforced by a trigger that rejects UPDATE/DELETE), migration `0002_domain_tables`.

`make types`: exports the schemas, then `json-schema-to-typescript` writes `frontend/src/api/generated/{dashboard-spec,draft-plan}.ts`.

Tests:

- Round-trip for every model (hypothesis strategies plus hand-written examples, including the R2 example).
- Validation rejects an unknown widget type, a duplicate widget id, a layout item pointing to a missing widget, and a measure expr outside the grammar (plus SQL-looking exprs such as `count_if(status = 'passed') / count()`).
- Property test: random valid op sequences on generated specs; applying the inverse returns the original exactly.
- Migration upgrade → downgrade → upgrade on an empty database; the append-only trigger rejects an UPDATE.
- Committed JSON Schemas equal a fresh export.

Risks: hypothesis strategies for a valid `DashboardSpec` are fiddly. I'll build them from a few composable generators rather than `st.builds` on the whole model.

## M3 — Ingestion and parsers

Files in `backend/app/ingestion/`:

- `detect.py`: `detect(filename, head) -> Detection(format, confidence, reason)`. Formats: csv, tsv, json, ndjson, xlsx, junit_xml, jtl_csv, jtl_xml, unknown.
- `parsers/`: `delimited.py`, `json_.py` (array, or object holding one array; loaded fully: documented limit), `ndjson.py` (lazy), `xlsx.py` (fastexcel; loaded fully), `junit.py` (`lxml.iterparse`, nested testsuites, properties), `jtl.py` (CSV via Polars lazy; XML via iterparse)
- `normalize.py`: canonical `test_results`, `perf_samples` and generic typed tables; status mapping; ms durations; UTC timestamps with an `orig_tz` column; build precedence (upload field → `<properties>` → filename number → upload order)
- `catalog.py`: `schema_hash(columns)` (sha256 of the name-sorted `(name, type)` list), register Dataset + DatasetVersion with parts, row count and lineage; append creates a version whose parts are the previous parts plus the new one; incompatible schemas are refused with a readable error (field mapping is M9)
- `errors.py`: `IngestionError(user_message)`; the job stores only `user_message`, never a traceback
- `context.py`: regex extraction of key=value and threshold facts
- `backend/app/jobs/ingest.py`: the RQ job (download → detect → parse → normalise → Parquet → object store → register)
- API: `api/uploads.py` (`POST /uploads`, `GET /uploads/{id}`), `api/sessions.py` (`POST /sessions`, `POST /sessions/{id}/context`), `api/datasets.py` (`GET /datasets/{id}`)

Upload streaming: chunks are read from the multipart stream, hashed with sha256 and sent to the object store as a multipart upload, aborting past the size limit (413).

Fixtures: `backend/tests/fixtures/generate.py` (deterministic, seeded) writes everything listed in plan M3; outputs are committed.

Tests:

- The detector picks the right format for each fixture with confidence ≥ 0.9; unknown text → `unknown` with a message.
- Row counts of parsed valid fixtures equal counts computed independently (raw line or element counts).
- Each malformed fixture → upload status `failed` with a readable message containing no "Traceback" or exception class names.
- Normalisation units: status mapping, ms conversion, UTC/timezone, build precedence.
- End to end through the API with a burst worker: upload → `GET /uploads/{id}` = done → dataset version registered with Parquet in the object store.
- Re-uploading a file with the same columns (reordered rows, different values) gives the same schema_hash; appending creates v2 with two parts.
- Context facts: `p95 must be < 800 ms, env=staging` → threshold `{metric: p95, op: <, value: 800, unit: ms}` and fact `{env: staging}`.
- Slow: a synthetic 200 MB CSV ingests in a subprocess whose peak RSS stays below half the file size.

Risks:

- Peak-RSS measurement differs by OS (`ru_maxrss` is bytes on macOS, KB on Linux): handled explicitly.
- Polars type inference on generic CSV can differ between a small sample and the full file; `infer_schema_length` is set and failures map to a readable error.

## Phase 1 exit criteria → verification

- `make dev` from a fresh clone: clone into a temp dir, copy `.env.example`, run `make dev`, curl `/health`.
- Every valid fixture uploads and becomes a registered Parquet dataset: API-level parametrised test over all valid fixtures.
- `make check` green locally and in CI: run locally; push the branch and check the run with `gh run watch`.
