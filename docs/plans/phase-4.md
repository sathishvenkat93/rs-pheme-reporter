# Phase 4 plan — User interface (M10, M11)

Branch `phase-4` (stacked on `phase-3`). The UI talks to the API through the Vite proxy (`/api/*` → `:8000`). Everything here runs with the offline FakeLLM (`PHEME_LLM=fake`), so the browser tests don't need an API key.

Libraries:

- `@tanstack/react-query` 5 for data fetching
- `echarts` 6 for charts
- `react-grid-layout` 2, which has a new TypeScript API: `GridLayout` with `width`, `gridConfig`, `dragConfig` and `useContainerWidth`
- `openapi-typescript` for API types
- `@playwright/test` for the browser tests

## Backend additions (small, for the UI)

- `GET /sessions/{id}/messages`: the chat history, so a reload restores the conversation.
- `SessionOut.dataset_ids`, for preview queries.
- `POST /sessions/{id}/options/{option_id}`, body `{included}`: the include/exclude toggle. It goes through `update_plan` semantics: Remove, or re-add with status requested.
- `make types` also dumps the OpenAPI schema and generates `src/api/generated/openapi.ts`.

## M10 — Upload, chat, options, approvals

- `src/api/client.ts`: a typed `fetch` wrapper over the OpenAPI `paths`, plus an XHR upload with progress.
- `src/charts/`: one pure function per widget type (12) mapping (widget spec, rows) to an ECharts option. Theme and status tokens; number formats (percent, ms, bytes, rate); empty-data and long-label handling. Plus a `<Chart>` component that wraps `echarts.init` (SVG renderer, which jsdom accepts).
- `features/session`: a SessionProvider (session id, state, SSE subscription through `EventSource`; events invalidate React Query caches) and a chat store (streamed assistant text, tool progress, plan diffs, approval cards, a confirm card, edit previews).
- `features/chat`:
  - a drop zone with upload progress and status polling
  - a context box
  - the chat panel: messages, streaming text, tool-progress lines, compact plan diffs, and quick-reply chips for clarifying candidates
- `features/proposal`:
  - option cards: title, chart type, fields, rationale, status badge, score breakdown on hover/focus, an include toggle, and a live thumbnail from `POST /query` (50 rows)
  - approval cards: reason, before/after, and Accept alternative / Keep as asked / Drop / Provide more data
  - an "Approve plan" button, disabled while decisions are open, with a tooltip naming them
- Routes: `/` creates a session and redirects to `/s/:sid`, which shows chat beside the proposal, or the dashboard once it's built.

Tests (Vitest):

- every renderer mapping
- the approval-button logic
- option-card toggling and accessible names
- reducing SSE events into the chat store

## M11 — Dashboard canvas, library, editing

- `/d/:id` and `/d/:id/v/:n`: a react-grid-layout canvas rendering each widget with `src/charts`. It has a global filter bar and loading, empty and error states per widget.
- Direct manipulation: drag, resize and inline rename produce widget-id patch ops (`src/features/dashboard/patches.ts`, pure), sent with PATCH and the `base_version`. On a 409 it refetches, then re-applies the ops if the widgets they touch are unchanged; otherwise it tells you what changed.
- Selection: click selects a widget and Shift-click adds more. A context chip ("Editing: …") appears in the chat and the selected ids go with the next message. Opening `/d/:id` starts a session on that dashboard.
- Agent edit previews show before and after with Apply (`POST /sessions/{id}/edit/apply`) and Adjust (keep chatting, or discard).
- A version-history drawer lists versions with author, time and note, with diff and restore.
- `/library`: search, filter by domain and tag, sort by last updated, pinned favourites (localStorage), open.
- Refresh: an "Upload new data" button (append upload, then `POST /dashboards/{id}/refresh`); a mapping proposal is shown when needed.
- PNG export composes each widget's `getDataURL()` onto one canvas.

Tests (Vitest): patch generation for drag, resize and rename; the 409 rebase path; library filtering and pins.

## Phase deliverable and e2e

- `frontend/e2e/smoke.spec.ts` (Playwright): upload JUnit → options appear → resolve the flag → approve → build → the dashboard renders.
- `make e2e` → `scripts/e2e.sh`: a separate `pheme_e2e` database (created and migrated), the API with `PHEME_LLM=fake`, the worker, and the Vite dev server, all through Playwright `webServer`.
- Chromium is installed by `pnpm exec playwright install chromium`; CI installs it too.

## Verification of the M10/M11 browser "Done when" items

Playwright specs drive the real app, in addition to the smoke test:

- M10: the flow up to approval, including resolving a flag
- M11: build, inline rename, chart-type change by chat, undo from history, reopen from the library with every version listed

These double as the "by hand" checks, and they run headless.

## Risks

- react-grid-layout v2 is new; tests may need `ResizeObserver` and `matchMedia` stubs in jsdom.
- ECharts in jsdom: renderer tests assert on option objects, not pixels; `<Chart>` is smoke-rendered with the SVG renderer.
- Playwright needs a browser download (about 150 MB).

## Deviations log

(appended during the phase)
- **M10** — Backend additions for the UI: `GET /sessions/{id}/messages`; `dataset_ids` and `pending_edit` on the session; `POST /sessions/{id}/options/{option_id}` (include toggle); `events?after=$` (new events only); decisions carry `candidates` and `before`/`after`.
- **M10** — Bug found while building the history endpoint: every message in one request had the same `created_at` (Postgres `now()` is the transaction time), so ordering, including the LLM's replayed history, depended on random UUIDs. Fixed with a monotonic `messages.seq` identity column (migration 0004).
- **M10** — Assistant replies are taken from the `POST /messages` response; SSE supplies progress, deltas, diffs, cards and invalidations.
- **M10** — Chrome 158 returns a Promise from `scrollIntoView`; an effect that returned it crashed React ("destroy is not a function"). Fixed, and routes now have an error boundary.
- **M10** — Proposal thumbnails use a compact chart variant (no axis labels or legends) because full axes didn't fit in a card.
