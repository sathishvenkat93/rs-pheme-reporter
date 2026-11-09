#!/usr/bin/env bash
# Runs the API, the RQ worker and the Vite dev server together. Ctrl-C stops all three.
set -euo pipefail
cd "$(dirname "$0")/.."

# macOS: RQ forks work-horses; without this, Objective-C runtime checks can abort them.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

trap 'trap - EXIT INT TERM; kill 0 2>/dev/null || true' EXIT INT TERM

(cd backend && uv run uvicorn app.main:app --reload --port 8000) &
(cd backend && uv run python -m app.jobs.worker) &
(cd frontend && pnpm dev) &

echo "API      http://localhost:8000/health"
echo "Frontend http://localhost:5173"
wait
