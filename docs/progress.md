# Progress

Status of the build plan in [PHEME_IMPLEMENTATION.md](../PHEME_IMPLEMENTATION.md). Tick a milestone when its "Done when" items are verified; tick the gate when the user approves the phase.

## Phase 1 — Foundation

- [x] M1 Bootstrap
- [x] M2 Models
- [x] M3 Ingestion
- [x] Gate passed (approved 2026-11-09; CI still unverified: push blocked by GitHub credentials)

## Phase 2 — Data intelligence

- [x] M4 Profiler
- [x] M5 Query
- [x] M6 Recommender
- [x] M7 Feasibility
- [x] Gate passed (approved 2026-11-09; CI still unverified: push blocked by GitHub credentials)

## Phase 3 — Agent and dashboards

- [x] M8 Orchestrator (live-LLM run pending: no API key configured)
- [x] M9 Dashboards & edits
- [x] Gate passed (approved 2026-11-09; CI and the live-LLM run still pending)

## Phase 4 — User interface

- [x] M10 UI chat & proposals
- [x] M11 UI dashboard & library
- [x] Gate passed (approved 2026-11-09; CI still unverified: push blocked)

## Phase 5 — Hardening and evaluation

- [x] M12 Security & audit
- [x] M13 Observability & limits
- [x] M14 Evaluation & e2e
- [ ] Gate passed

## Notes

- 2026-11-09: Plan corrected before Phase 1 (commit "docs(plan): resolve contradictions…"). MinIO images are no longer published; SeaweedFS replaces it locally (user decision).

## Evaluation (first numbers, 2026-11-09)

`make eval` on the three seed goldens (JUnit, JMeter, daily CSV), offline (FakeLLM; curation uses its deterministic fallback). Full report: [backend/eval/reports/2026-11-09.md](../backend/eval/reports/2026-11-09.md).

| Metric | Value | Gate 1 / R10 target | |
| --- | --- | --- | --- |
| Parse coverage | 100% | ≥ 98% | ✅ |
| Recommendation overlap (curated top 8 vs experts) | 62.5% | ≥ 70%, or agreement − 10 pts = 46% | ✅ (relative rule) |
| Expert-to-expert agreement | 56% | baseline | — |
| Spec validity | 100% | ≥ 99% | ✅ |
| Feasibility precision / recall (can't-build) | 100% / 90.9% | ≥ 95% / ≥ 90% | ✅ |
| Edit success (automated proxy) | 100% | ≥ 90% (human-rated) | ✅ proxy; human rating pending |
| Time to first proposal (50 MB) | 0.67 s | < 30 s | ✅ (excludes upload and live LLM latency) |
| Dashboard reopen from cache | < 10 ms | < 2 s | ✅ |

**Gate 1** ("pilot datasets produce valid dashboards, and feasibility precision ≥ 95%") is met **on the seed goldens only**. The expert lists were written by the build agent and the data is synthetic, so this isn't yet evidence about pilot data. It needs pilot datasets with independent expert labels, plus one live-LLM run.

Live-LLM run: not yet recorded (no API key configured). Command: `PHEME_LIVE_LLM=1 uv run pytest tests/orchestrator/test_live_llm.py` and `PHEME_LIVE_LLM=1 make eval`.
