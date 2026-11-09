#!/usr/bin/env bash
# make e2e: an isolated stack (own DB, Redis DB, bucket, queue; offline FakeLLM) + Playwright.
set -euo pipefail
cd "$(dirname "$0")/.."

export PHEME_DATABASE_URL="postgresql+psycopg://pheme_app:pheme_app@localhost:5432/pheme_e2e"
export PHEME_MIGRATION_DATABASE_URL="postgresql+psycopg://pheme:pheme@localhost:5432/pheme_e2e"
export PHEME_REDIS_URL="redis://localhost:6379/14"
export PHEME_S3_BUCKET="pheme-e2e"
export PHEME_QUEUE_NAME="pheme-e2e"
export PHEME_LLM="fake"
export PHEME_LOCAL_CACHE_DIR="${TMPDIR:-/tmp}/pheme-e2e-cache"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export E2E_API_PORT="${E2E_API_PORT:-8010}"
export E2E_WEB_PORT="${E2E_WEB_PORT:-5180}"

(
  cd backend
  uv run python - <<'PY'
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.core.config import get_settings
from app.core.storage import get_storage
from app.core.redis import get_redis

url = make_url(get_settings().migration_database_url)
admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as c:
    c.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    c.execute(text(f'CREATE DATABASE "{url.database}"'))
s3 = get_storage()
if not s3.bucket_exists(get_settings().s3_bucket):
    s3.make_bucket(get_settings().s3_bucket)
get_redis().flushdb()
PY
  uv run alembic upgrade head >/dev/null
)
rm -rf "$PHEME_LOCAL_CACHE_DIR"

(cd backend && exec uv run python -m app.jobs.worker) > "${TMPDIR:-/tmp}/pheme-e2e-worker.log" 2>&1 &
WORKER=$!
trap 'kill $WORKER 2>/dev/null || true' EXIT

cd frontend && pnpm exec playwright test "$@"
