# Phase 2 report — Data intelligence

Branch `phase-2` (branched from `phase-1`, which isn't merged yet). Commits: `d5e40b2` (M4), `e75dd15` (M5), `7fab347` (M6), `f019e1c` (M7), `fdf45f9` (demo-data).

**Status:** built and verified locally. CI is still unverified, because the GitHub push remains blocked by credentials (same as Phase 1). There are no LLM calls anywhere in this phase.

## What was built

- **M4 — Profiler and domain packs.**
  - A sandboxed DuckDB engine over locally cached Parquet parts (only the version's files are readable, the configuration is locked, the session is in UTC).
  - Profiles: per-column role, nulls, distinct count, numeric stats, top-10 values and time stats; per dataset, keys, hierarchies (e.g. suite → case), correlations, IQR outliers, Mann-Kendall trends and change points.
  - Role and domain rules written as data tables.
  - A ≤ 5 KB LLM view of the profile.
  - YAML domain packs (full test_execution and performance; stubs for app_logs, cicd and generic).
  - `GET /datasets/{id}/profile`, cached.
- **M5 — Semantic query service.**
  - The compiler turns a SemanticQuery into parameterised SQL: every identifier is checked against the schema, every value is a bound parameter. It covers the whole measure grammar (count_if, percentile, ratio, rate, apdex, flaky_rate), all five filter types, minute-to-week and build grains, top N with a recomputed "Other", and numeric ordering for build-like text.
  - The executor adds a single-SELECT guard, a timeout, a row cap and a Redis cache.
  - LTTB downsampling and `POST /query`.
- **M6 — Recommendation engine.**
  - Rules for every R3 row plus the domain pack's signature visuals.
  - Guard rules enforced at generation and checked independently.
  - R4 scoring with configurable weights and a cumulative redundancy penalty.
  - Requested charts pinned first, through a resolver hook.
- **M7 — Feasibility and decisions.**
  - The field resolver tries exact names, then known derived fields and metrics, then vague concepts, then pack synonyms, then fuzzy matching.
  - The R5 checks run in order; adjustments carry before/after values; reasons are plain and under 25 words.
  - Decisions support every choice, with keep_as_asked only when the original request is buildable; `can_build` reports open decisions.
- **Deliverable:** `make demo-data` (`scripts/demo_data.py`).

## Verification

| Item | How | Result |
| --- | --- | --- |
| M4: profile snapshots for every fixture | 7 snapshot tests (stable across repeated runs) | ✅ |
| M4: JUnit → test_execution, JTL → performance, generic CSV → generic | `test_domain_classification` (also events → app_logs, deployments → cicd) | ✅ |
| M4: JTL profile < 1 s | Timed test; measured ~47 ms | ✅ |
| M4: 50-column profile ≤ 5 KB | Synthetic 50-column dataset; LLM view measured | ✅ |
| M5: golden pass rate by build and p95 per label match Polars | `tests/query`; also flaky rate, rate and apdex golden tests | ✅ |
| M5: injection refused | Malicious field names → "no field named"; malicious values bound (0 rows, table intact); `read_csv('/etc/passwd')`, other Parquet, https, INSTALL, SET, COPY refused on executor connections; stacked statements, DROP, INSERT and CREATE refused by the single-SELECT guard | ✅ |
| M5: top N + Other sums to the total | Hypothesis property tests: additive sums equal the total; for non-additive measures, "Other" equals the measure over the excluded rows | ✅ |
| M5: cached query doesn't touch DuckDB | Spy on `engine.connect`: one call for two executions | ✅ |
| M6: JUnit top 10 includes pass-rate KPI, pass rate by build, failures by error type, duration by suite | `test_junit_top_10` and demo-data | ✅ |
| M6: JTL top 10 includes p95 over time, response-time histogram, throughput over time, errors by label | `test_jtl_top_10` and demo-data | ✅ |
| M6: no candidate violates a guard rule | Hypothesis over generated profiles (150 examples, 8 extra seeds checked) plus every fixture | ✅ |
| M6: a requested valid chart ranks in the top 5 | 3 requests on 3 fixtures (pinned at #1) | ✅ |
| M6: every candidate compiles | Every candidate on all 6 fixture sets compiles **and returns rows** | ✅ |
| M7: table-driven test for every R5 row | 8 rows with expected verdict, check name and alternative or applied change; alternatives pass their own check | ✅ |
| M7: open decision → can_build false; resolving the last flips it | `test_open_decisions_block_the_build_until_the_last_is_resolved` | ✅ |
| M7: reasons < 25 words, no jargon | Word count plus a jargon word list over all R5 reasons | ✅ |
| Exit: `make demo-data` matches the M6/M7 expectations | `tests/test_demo_data.py` checks the script's output | ✅ |
| Exit: `make check` green | 281 backend + 4 frontend tests, ruff, mypy, eslint, tsc; `make test-slow` passes | ✅ locally; CI ⏳ |

## Deviations from the plan

All of these are logged in `docs/plans/phase-2.md`.

1. **Model changes from M2.**
   - `VisualOption.options` added (bins, stacking, horizontal bars).
   - `DraftPlan.requests` added, and `Decision.item` may name a request, because a not-feasible request has no option for its decision to point at.
   - Pack `derived` entries gained `kind: field | metric`.
   - Schemas and TypeScript types are regenerated.
2. **Three fuzzy-match bands** instead of one threshold. With one threshold, "CPU usage" would have triggered a clarifying question instead of "this data has no CPU usage".
3. **Volume adjustment samples 10,000 points** (the renderer guard), not R5's 50k, because binned scatter isn't an MVP widget.
4. **The redundancy penalty is cumulative**, with a similarity scale I tuned; values are in the deviations log. KPI ties break by the order KPIs are listed in the domain pack.
5. **DuckDB sessions are pinned to UTC**, and the **executor refuses anything but a single SELECT**. A raw DuckDB connection does run stacked statements; the compiler never produces them.
6. **Dependencies added:** numpy, pyyaml, pytz.

**Bugs the tests caught and I fixed during the phase:**
- `last_n` sorted builds ascending.
- Host-timezone bucketing.
- An ORDER BY referred to the source column instead of the "Other"-folded output.
- The compiler and the guard disagreed on which dimension top N applies to.
- Pack measure ids clashed with real column names.
- Distribution charts were offered on fewer than 30 rows.

## Open issues

1. **GitHub push still blocked**, so there's no CI run and no PR for Phase 1 or 2. `phase-2` sits on top of `phase-1`, so one push sends both.
2. Scores, and therefore the top-10 lists, are tuned on synthetic fixtures. Real pilot data may need weight tuning (weights are in `PHEME_RECOMMEND_WEIGHTS`).
3. Insight for computed measures (pass rate, throughput) is a neutral 0.5–0.55, because the profile only holds trends of raw columns. Running a query at recommend time would sharpen it; that's a candidate for M8 curation.
4. Profile snapshots round to 6 significant digits. If Linux CI differs in the last digits, the rounding may need loosening.

## Decisions for you

1. **Unblock GitHub**, unchanged from Phase 1: `gh auth refresh -h github.com -s workflow`, or load an SSH key.
2. **Provide evaluation data (R10).** The pilot datasets and expert top-8 picks requested at this gate are needed for M14. One or two experts per dataset would give an agreement baseline. They don't block Phase 3.
3. **Anthropic API key for Phase 3.** M8 needs one recorded live LLM run. Put `PHEME_ANTHROPIC_API_KEY` in your local `.env` (never committed), or tell me to skip the live run for now.
