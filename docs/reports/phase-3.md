# Phase 3 report — Agent and dashboards

Branch `phase-3` (stacked on `phase-2` and `phase-1`; none merged, because the GitHub push is still blocked). Commits: `3419b13` (M8), `a6f17b9` (M9).

**Status:** built and verified locally with FakeLLM. Two exit items are still open:

- **The live-LLM run.** No API key is configured.
- **CI.** The push is blocked.

## What was built

**M8 — Agent orchestrator and chat API**

- **LLM layer.**
  - `AnthropicLLM` uses `claude-sonnet-5-5` (main, effort medium) and `claude-haiku-5-5` (fast, effort low). It enables the server-side refusal fallback (`fallbacks: "default"`) on the main model, caches the frozen system prompt and fixed tool order, uses structured outputs for the fast tasks, never forces `tool_choice` (a 400 on these models), and treats a refusal as an error.
  - `FakeLLM` matches rules on task, state, user-message pattern and offered tools, and records every request.
  - The tool loop is a manual loop over an append-only history. It re-reads the tool list every iteration and returns an error result for any tool the current state doesn't offer.
- **State machine.** Transitions are guarded in code, and each state exposes its own tools (R7). `build_dashboard` exists only in APPROVED. The only way into APPROVED is `POST /sessions/{id}/approve`; the model can at most show a confirm card.
- **Tasks.** Intent parsing, domain confirmation and context extraction use the fast model; curation (6–10 picks, ordered KPI → trends → breakdowns → distributions → detail) uses the main model. Every one is validated, retried once, then falls back deterministically.
- **Feasibility in the plan.** `check_feasibility` records the request, its option and its decision, so nothing user-requested enters the plan unchecked (rule 4).
- **Redaction and data blocks.** Reversible PII and secret redaction is stored per session, and placeholders are restored before anything is written. All data travels in `<data>` blocks.
- **API.** Sessions (including reopening a dashboard), messages, SSE events over Redis Streams with `Last-Event-ID` replay, approve, plans, decisions, and context with LLM extraction.
- **Offline policy.** The default `PHEME_LLM=fake` uses a heuristic intent parser and a scripted agent that calls the real tools, so the whole app runs without a key.

**M9 — Dashboard builder, versions and edit planner**

- **Builder.** Turns the approved plan into a spec: stable widget ids, merged measures, a live dataset binding, provenance (option and decision ids), KPI targets from context, and the 12-column auto-layout. It validates with the schema, the chart guards and a query dry-run, then runs and caches every query.
- **Store.** Versions are append-only. PATCH takes a base version and returns 409 on a stale base, using a row lock plus the unique version constraint. Restore makes a new version. Diffs are RFC 6902. Every write is audited with its ops and the originating prompt.
- **API.** Library search, latest version, version list and single version, per-widget data with global filters, diff, restore, patch, and refresh. Refresh proposes a field mapping when an upload doesn't fit the dataset.
- **Edit planner.**
  - `resolve_widget` works from the selection, the user's words or a position.
  - `patch_dashboard` validates the edit and shows a before/after preview; nothing is saved until the user clicks Apply.
  - `revert_dashboard` handles undo.
  - The offline policy covers rename, change chart type and undo.

## Verification

| Item | How | Result |
| --- | --- | --- |
| M8: FakeLLM JUnit session reaches PROPOSE with curated options | `test_junit_session_reaches_propose_with_curated_options` (6–10 recommended, ordered by kind, KPI first) | ✅ |
| M8: "pie of failures by test" → needs_approval; approval refused while open, accepted after | `test_flagged_request_blocks_approval_until_resolved`; also keep_as_asked swaps in the original donut | ✅ |
| M8: `build_dashboard` never offered before APPROVED | Every captured converse request before approval lacks it; APPROVED requests have it and lack `update_plan` | ✅ |
| M8: no LLM output can approve | A scripted model claims approval and calls `build_dashboard` / `request_approval`; the state stays REFINE and only a confirm card is shown | ✅ |
| M8: seeded secrets never appear in LLM requests | Emails, an IPv4 address, a bearer token, an AWS key, a JWT, a GitHub token and long hex were seeded into a JUnit file, the context and chat; none appear in any captured request | ✅ |
| M8: live run recorded | `tests/orchestrator/test_live_llm.py` is ready; skipped (no key) | ⏳ |
| M9: build from the M8 session → valid spec, every widget returns data | `test_build_yields_a_valid_spec_where_every_widget_returns_data` | ✅ |
| M9: one FakeLLM edit per R8 row; undo restores byte-for-byte | 6 parametrised scopes (widget data, visuals, content, layout, dashboard, bulk as one version); each previewed, applied, then undone; the body is byte-identical to the pre-edit version | ✅ |
| M9: concurrent patches on one base → one 200, one 409 | Two threads PATCH the same base: `[200, 409]` | ✅ |
| M9: second JUnit upload adds its build to pass rate by build | Live refresh: builds `[101..104]` become `[101..105]` | ✅ |
| Exit: API-level FakeLLM flow (session → … → refresh) | `tests/orchestrator/test_phase3_flow.py` | ✅ |
| Exit: `make check` green | 312 backend + 4 frontend tests, lint and types; `make test-slow` passes | ✅ locally; CI ⏳ |

## Deviations from the plan

All of these are logged in `docs/plans/phase-3.md`.

1. **Live run pending (no API key).** `AnthropicLLM` follows the SDK documentation but hasn't been exercised against the API. If the API rejects the `fallbacks` parameter, the client retries once without it and logs a warning.
2. **Offline policy as the default LLM**, so the app works without a key. Phase 4's browser tests will rely on it.
3. **Assistant "streaming" is chunked final text** (`assistant_delta` events), not token streaming. The event shape stays the same if real streaming is added later.
4. **Edits commit only when the user clicks Apply**, but undo commits immediately because the user asked for it directly. Chat on a published dashboard enters EDIT.
5. **Model and database additions:** `VisualOption` gained `thresholds`, `annotations` and `insight`; `sessions` gained `redactions` and `pending_edit` (migration 0003). Decision ids are UUIDs.
6. **Refresh is an endpoint** (`POST /dashboards/{id}/refresh`) after an append upload. Incompatible uploads get a field-mapping proposal.
7. **Bugs caught by the tests and fixed:**
   - IPv4 placeholders were never restored (the restore regex rejected kinds containing digits)
   - undo lost its reply when the state changed mid-turn
   - percent thresholds became KPI targets of 95 instead of 0.95

## Open issues

1. **GitHub push blocked**, so there's no CI run and no PRs for Phases 1–3. One push will send all three stacked branches.
2. **Live-LLM run not done.** Real-model behaviour (tool-call quality, structured-output schema acceptance, curation quality) is unmeasured. The tool and structured-output schemas are Pydantic-generated and closed (`additionalProperties: false`); if the API rejects a construct, those schemas are the first place to look.
3. **The offline intent parser is heuristic.** It's meant for tests and demos, not real users.
4. **Single-dataset sessions.** Recommendations, feasibility and builds use the session's first dataset; multi-dataset dashboards aren't covered yet.
5. **Edits don't re-check feasibility the way M7 checks new chart requests.** Patches are validated by schema, chart guards and query dry-run, which covers buildability; vague wording is resolved by `resolve_widget` before a patch is proposed.

## Decisions for you

1. **Unblock GitHub** (unchanged): `gh auth refresh -h github.com -s workflow`, or load an SSH key.
2. **Live run.** Add `PHEME_ANTHROPIC_API_KEY` to `.env`; I'll run `PHEME_LIVE_LLM=1 uv run pytest tests/orchestrator/test_live_llm.py` and record the transcript and cost in `docs/progress.md`. Or accept it as pending.
3. **Accept the edit-commit model** (Deviation 4): preview, then Apply, with immediate undo.
