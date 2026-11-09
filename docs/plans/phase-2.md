# Phase 2 plan — Data intelligence (M4–M7)

Branch `phase-2`, created from `phase-1` (not yet merged: the GitHub push is blocked). No LLM calls anywhere in this phase.

Shared foundation (built in M4, hardened in M5): `app/query/engine.py` downloads a dataset version's Parquet parts into a local read-through cache (`PHEME_LOCAL_CACHE_DIR`; parts are immutable, so the object key is the cache key). It then opens a DuckDB connection with a `data` view over those parts, `allowed_paths` limited to them, external access disabled and configuration locked. Verified up front: `read_csv('/etc/passwd')`, other Parquet paths, https URLs, `INSTALL`, `ATTACH`, `COPY` and re-enabling access are all refused.

## M4 — Profiler and domain packs

Files: `profiling/roles.py` (role rules as a data table), `profiling/profiler.py`, `profiling/domain.py`, `profiling/llm_view.py`, `domain_packs/{loader.py, test_execution.yaml, performance.yaml, app_logs.yaml, cicd.yaml, generic.yaml}`, `api/datasets.py` (`GET /datasets/{id}/profile`, cached in the `profiles` table).

Interfaces:

- `profile_version(db, dataset_version_id) -> DatasetProfile`
- `profile_paths(paths, kind, version_id) -> DatasetProfile` (pure; used by tests and demo-data)
- `infer_role(name, type, stats) -> ColumnRole`
- `classify(profile) -> (domain, confidence)`
- `load_pack(domain) -> DomainPack`
- `llm_view(profile) -> dict`

Profile contents:

- Per column: role, null %, distinct count, min/max/mean/p50/p95/skew, top-10 values, and for time columns span, median gap and a monotonic flag.
- Per dataset: grain guess, single-column and pair candidate keys, hierarchies (functional dependencies parent → child between category/identifier columns, e.g. suite → case), measure correlations, IQR outlier counts, and trends (Mann-Kendall on at most 500 time buckets, so strength = |τ|).
- Change points: one-split binary segmentation, reported when the effect size is > 1.

Tests: snapshot of every valid fixture's profile (`tests/profiling/snapshots/`, refresh with `PHEME_UPDATE_SNAPSHOTS=1`); role rules table-driven; domain classification (JUnit → test_execution, JTL → performance, daily-metrics CSV → generic); JTL profile < 1 s; synthetic 50-column profile → LLM view ≤ 5 KB; packs validate.

## M5 — Semantic query service

Files: `query/compiler.py`, `query/executor.py`, `query/cache.py`, `query/lttb.py`, `api/query.py`.

- `compile(query, columns, measures, time_field) -> Compiled(sql, params)`. Identifiers are checked against the schema and double-quoted; every literal is a bound parameter. Measures:
  - `count_if` → `count(*) FILTER (WHERE …)`; `percentile` → `quantile_cont`; `ratio` → `a / NULLIF(b, 0)`
  - `rate` → divided by the bucket length (with a time grain) or by the filtered time span
  - `apdex` → standard satisfied + tolerating/2
  - `flaky_rate` → a window flag per (build, suite, case) over the filtered rows
- Filters: `last_n` keeps the top n values in ordinal order (numeric when castable); relative time is anchored to the data's max timestamp by default.
- top_n: "Other" is the measure recomputed over the excluded rows, so additive measures sum to the total.
- `execute()`: 30 s timeout via `interrupt()` on a timer, a row cap from config, and a result cache in Redis keyed by (query_hash, dataset_version_id).
- `POST /query` is workspace-scoped.

Tests: golden values vs Polars (pass rate by build; p95 per label, linear interpolation); injection (SQL in field names and filter values; raw file/URL functions on an executor connection); top_n property tests (additive sums, non-additive "Other" = measure over excluded rows); cache spy; LTTB keeps endpoints and size.

## M6 — Recommendation engine

Files: `recommend/grammar.py` (one declarative rule per R3 row), `recommend/measures.py` (measure catalog from profile + pack), `recommend/scoring.py` (R4; weights in `PHEME_RECOMMEND_WEIGHTS`), `recommend/engine.py`.

- `recommend(profiles, requests=None, k=25)` → ranked `VisualOption`s with breakdowns and facts. Domain-pack KPIs and signature visuals become candidates.
- Requested charts are pinned above everything else. M6 resolves request fields by exact name; M7 plugs in the full resolver and feasibility check through a `RequestResolver` hook.

Tests: JUnit and JTL top-10 expectations (from the plan); hypothesis property test that no candidate breaks a guard over generated profiles; a requested valid chart is in the top 5; every candidate's query compiles (and runs on the fixtures).

## M7 — Feasibility validator and decisions

Files: `feasibility/resolver.py`, `feasibility/checks.py`, `feasibility/decisions.py`.

- The resolver tries exact names, then pack synonyms (to columns or pack measures), then rapidfuzz, then concept ambiguity ("performance" → latency / throughput / errors ⇒ needs_clarification with the top 3).
- `check(request, profile)` runs R5 in order. Adjusted results modify the working request and continue; not_feasible and needs_clarification stop. Known derived metrics (e.g. MTTR) skip the field-exists step and are judged in Derivation.
- Decisions:
  - `create_decisions(plan, verdicts)`
  - `resolve(decision, choice, user)` — keep_as_asked only when the original request is buildable
  - `can_build(plan)`

Tests: table-driven over every R5 row; the can_build flip; every reason < 25 words and free of a jargon word list.

## Phase deliverable

`make demo-data` → `scripts/demo_data.py`. For each valid fixture it prints the profile summary, domain, top 10 with score breakdowns, and R5 verdicts (hand-written ChartRequests, each run against the fixture where it is meaningful; the Volume row uses a synthetic 8M-row profile).

## Risks

- Scoring weights need tuning to meet the M6 top-10 expectations; any tuning stays inside config, never special-cased per fixture.
- `flaky_rate` and `rate` need care with filters and grains.

## Deviations log

(appended during the phase)
- **M4** — `app/query/engine.py` (sandboxed DuckDB + local part cache) was built in M4 because the profiler needs it; M5 reuses it.
- **M4** — Candidate keys come only from identifier, category and time columns (unique float measures such as revenue aren't keys).
- **M4** — Added `numpy` (Mann-Kendall, segmentation) and `pyyaml` (domain packs) as dependencies.
