# Phase 1 report — Foundation

Branch `phase-1` · commits `bf64a7d` (M1), `170f0a0` (M2), `1145697` (M3), `a15999c` (M3 fix), on top of the corrected plan (`f167985`).

**Status: built and verified locally. CI not yet verified.** The push to GitHub is blocked by credentials (see Open issues), so the "green in CI" exit criterion is still open.

## What was built

- **M1 — Bootstrap.** FastAPI app with `GET /health` checking Postgres, Redis and the object store; pydantic-settings config; a dev `current_principal`. Docker Compose with Postgres 17, Redis 7 and SeaweedFS, plus an idempotent bucket init. Alembic baseline and an RQ worker with a no-op job. Vite + React + TS + Tailwind shell (chat | canvas) with a `/library` route. Makefile, GitHub Actions workflow, `CLAUDE.md`, a 14-line README and `docs/progress.md`.
- **M2 — Domain models.**
  - The measure grammar: parser to AST, canonical formatter, readable errors. No eval, no SQL.
  - Pydantic models for datasets, versions and profiles; SemanticQuery and its five filter types; ChartRequest; VisualOption; Verdict; DraftPlan, PlanOp and Decision; DashboardSpec with integrity validators.
  - Widget-id patch ops with RFC 6902 translation and exact inverses.
  - JSON Schema export and `make types`.
  - 15 tables, migration `0002`, and an append-only trigger on `dashboard_versions`.
- **M3 — Ingestion.**
  - A format detector.
  - Parsers for CSV/TSV, JSON, NDJSON, xlsx, JUnit XML (nested suites, properties) and JMeter JTL (CSV and XML).
  - Canonical `test_results` and `perf_samples` tables plus a typed generic table.
  - Bounded-memory chunked reads for line-based formats.
  - An append-mode dataset catalog.
  - Endpoints: `POST /sessions`, `POST /sessions/{id}/context` (regex facts), `POST /uploads`, `GET /uploads/{id}`, `GET /datasets/{id}`.
  - Deterministic fixtures with a manifest.

## Verification

| Item | How it was verified | Result |
| --- | --- | --- |
| M1: `make dev` starts everything, `/health` ok for all three | Ran `make dev`; curl `/health` → `{"status":"ok", ... all "ok"}`; worker log "Listening on pheme"; Vite serves `<title>Pheme</title>` | ✅ |
| M1: `make check` passes locally | `make check` | ✅ |
| M1: `make check` passes in CI | Push rejected (credentials) | ⏳ not verified |
| M1: fresh clone + `.env` + `make dev`, no other steps | Cloned into the scratchpad, copied `.env.example`, ran `make dev`, curled `/health` (re-run at the gate on final code) | ✅ |
| M2: round-trip for every model | `tests/models/test_roundtrip.py`: 25 examples covering every model, plus 50 generated specs | ✅ |
| M2: rejects unknown widget type, duplicate widget id, layout→missing widget, measure expr outside grammar | `tests/models/test_spec_validation.py` (the old SQL-like R2 expr is among the rejected ones) | ✅ |
| M2: patch then inverse returns the original exactly | Hypothesis property test, 200 random op sequences; compares dicts and JSON bytes | ✅ |
| M2: migration applies and rolls back on an empty DB | `tests/db/test_migrations.py`: upgrade → downgrade → upgrade on a throwaway DB, `alembic check` clean | ✅ |
| M3: detector right with confidence ≥ 0.9 for every fixture | `tests/ingestion/test_detect.py`, all 19 fixtures incl. malformed; `app.log` → unknown | ✅ |
| M3: 100% of rows in valid fixtures parse; malformed → readable error | Row counts equal the generator's manifest; malformed messages checked for tracebacks, exception class and library names | ✅ |
| M3: 200 MB CSV without loading fully into memory | `make test-slow`: 50 MB → +144 MiB RSS, 201 MB → +167 MiB (see Deviations for the metric) | ✅ |
| M3: re-upload with same columns → same schema_hash | API test: original twice plus a variant with other rows | ✅ |
| Exit: every valid fixture uploads and becomes a registered Parquet dataset | Parametrised API test over all 11 valid fixtures (Parquet part checked in the store); also by hand against the running dev server with the forking RQ worker | ✅ |
| Exit: `make check` green locally and in CI | Local ✅ (174 backend + 4 frontend tests, ruff, mypy, eslint, tsc). CI ⏳ | partial |

## Deviations from the plan

Plan corrections made before Phase 1 (with your answers) are in commit `f167985`. During the phase:

1. **SeaweedFS instead of MinIO.** MinIO's images are no longer published (your decision). The S3 identity is inlined via Compose `configs` instead of a bind mount, which makes it work outside `$HOME` with Colima.
2. **CI starts `docker-compose.yml`** instead of GitHub service containers, which can't pass the store's `server` command.
3. **Pydantic lax mode, not `strict=True`.** FastAPI validates request bodies in Python mode, where strict mode rejects UUID, datetime and enum strings. I verified this with a test endpoint. Unknown keys are still rejected and every invariant is a validator.
4. **`DatasetBinding.dataset_id` added.** The R2 example had no way to name the actual dataset.
5. **Chunked CSV/NDJSON reading** (2 MiB chunks, schema fixed by the first chunk) instead of Polars `scan_csv` + `sink_parquet`. Measured: Polars' sink grew about 540 MiB at 200 MB and about 740 MiB at 400 MB; PyArrow's streaming reader also grew. Chunked reads stay flat.
6. **The slow test measures marginal growth** (50 MB vs 200 MB) and "peak growth < file size". My first draft's bound of "< half the file size" couldn't pass any design, because about 130 MiB is fixed engine and allocator overhead. This replaces a bound I had written myself; it does not loosen a requirement in the plan.
7. **JSON documents and .xlsx are loaded fully.** The 200 MB guarantee covers line-based formats only.
8. **Tooling:** TypeScript pinned to 6.0, because TS 7 has no JS API and typescript-eslint can't use it. eslint replaces the template's oxlint. Ruff E501 is off; `ruff format` governs line length.
9. **`GET /datasets/{id}` added** (not in the plan) for tests and later milestones.

## Open issues

1. **GitHub push is blocked**, which blocks CI and the PR. No SSH key is available to this shell, and the `gh` token lacks the `workflow` scope that pushing `.github/workflows/ci.yml` needs.
2. Failed uploads can leave orphaned objects in the store if the DB commit fails after the upload. That's a cleanup job, not urgent.
3. `dashboard_versions` is append-only, so cascading deletes from `workspaces` will be refused by the trigger. M12 should decide on a deletion policy (soft delete, or an admin bypass).
4. FastAPI spools multipart uploads to a temp file before Pheme streams them on, which writes large files to disk twice. That's acceptable at 200 MB.
5. Local environment changes outside the repo: Homebrew installed node, pnpm, colima, docker and docker-compose; Colima is running (4 CPU, 6 GiB); `~/.docker/config.json` was created to register the compose plugin; uv installed Python 3.12.

## Decisions for you

1. **Unblock GitHub** with one of these, then I'll push, watch CI and open the PR:
   - `gh auth refresh -h github.com -s workflow`, or
   - make an SSH key with GitHub access available (e.g. `ssh-add`).
2. **Accept Pydantic lax mode** (Deviation 3), or ask for strict models with a custom body parser.
3. **Accept the memory-test metric** (Deviation 6).
