# Pheme

Chat-driven agent that turns logs and statistics into dashboards. You upload a file, Pheme profiles it and proposes charts, you refine the plan in chat and approve it, and Pheme saves a versioned dashboard you can reopen and edit later.

Build plan: [PHEME_IMPLEMENTATION.md](PHEME_IMPLEMENTATION.md). Status: [docs/progress.md](docs/progress.md).

**Supported files:**
- CSV/TSV
- JSON and NDJSON (one JSON object per line, whatever the file extension)
- Excel (`.xlsx`)
- JUnit/xUnit XML
- JMeter JTL (CSV or XML)

Plain-text logs, such as `2026-11-09 12:00:01 ERROR ...`, aren't supported yet.

---

## Run it locally

What runs where:

| Part | How it runs | Address |
| --- | --- | --- |
| PostgreSQL 17 | Docker | `localhost:5432` |
| Redis 7 | Docker | `localhost:6379` |
| SeaweedFS (S3-compatible file store) | Docker | `localhost:8333` |
| API (FastAPI) | On your machine | http://localhost:8000 |
| Background worker (processes uploads) | On your machine | — |
| Web app (Vite + React) | On your machine | http://localhost:5173 |

Docker only runs the three backing services. The API, worker and web app run on your machine.

### 1. Install the prerequisites

You need:
- Docker with Compose v2
- [uv](https://docs.astral.sh/uv/), which also installs Python 3.12 for you
- Node.js 24 or later
- pnpm
- `make`

**macOS (Homebrew):**

```bash
brew install uv node pnpm
# Docker: either install Docker Desktop or OrbStack, or use Colima:
brew install colima docker docker-compose
colima start
```

If `docker compose version` fails after installing the Homebrew `docker-compose` package, add the plugin folder to `~/.docker/config.json`:

```json
{ "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"] }
```

**Linux:** install Docker Engine with the Compose plugin, plus uv, Node 24 and pnpm (`npm install -g pnpm`).

**Windows:** use WSL 2 with Docker Desktop, and follow the Linux steps inside WSL.

Check everything is in place:

```bash
docker compose version && uv --version && node --version && pnpm --version
```

### 2. Create your `.env`

```bash
git clone <repo-url> rs-pheme-reporter
cd rs-pheme-reporter
cp .env.example .env
```

**For a first local run you don't need to change anything.** The defaults in `.env.example` match `docker-compose.yml`. The agent runs offline with a built-in scripted model (`PHEME_LLM=fake`), so no cloud account is needed.

Only change values for one of these reasons:

| You want to… | Change in `.env` |
| --- | --- |
| Use the real Claude models | The LLM block. See [Use the real models (Amazon Bedrock)](#use-the-real-models-amazon-bedrock). |
| Avoid a port that's already in use (e.g. a local Postgres on 5432) | The port **and** the URLs that use it. See [Changing ports](#changing-ports). |
| Sign in by email instead of the fixed dev user | `PHEME_AUTH_MODE=dev`. A sign-in dialog then asks for any `name@domain.tld`; users with the same email domain share a workspace. |
| Allow bigger uploads | `PHEME_MAX_UPLOAD_BYTES` (default 200 MB) |
| Read logs as plain text instead of JSON | `PHEME_LOG_FORMAT=text` |

`.env` is git-ignored. Never commit it.

### 3. Start everything

```bash
make dev
```

This runs these steps:
1. **`make install`:** backend packages (`uv sync`) and frontend packages (`pnpm install`).
2. **`make deps-up`:** starts Postgres, Redis and SeaweedFS in Docker, waits until they're healthy, and creates the `pheme` bucket.
3. **`make migrate`:** creates the database tables, the `pheme_app` role and the row-level security policies.
4. **`scripts/dev.sh`:** starts the API, the worker and the web app together.

The first run downloads the Docker images and packages, so it takes a few minutes. When it's ready you'll see:

```
API      http://localhost:8000/health
Frontend http://localhost:5173
```

Check the API with `curl localhost:8000/health`.

### 4. Use it

1. Open http://localhost:5173.
2. Drop a file (CSV, JSON, Excel, JUnit XML or JMeter JTL) onto the upload area. Optionally add context in the text box, such as "pass rate should be at least 95 %".
3. Review the proposed charts. Ask for specific charts or changes in chat, and resolve any request Pheme flags as not buildable.
4. Click **Approve plan**. The dashboard is built and saved.
5. Reopen it any time from the **Library**, and edit it by chat or directly on the canvas.

### 5. Stop and restart

- **Ctrl-C** in the `make dev` terminal stops the API, worker and web app.
- `make deps-down` stops the Docker services. Your data is kept in Docker volumes.
- `make dev` starts everything again.
- **Wipe all local data** (dashboards, uploads, database): `docker compose down -v`. This cannot be undone.

---

## Use the real models (Amazon Bedrock)

The models are Claude Sonnet 5.5 (planning) and Claude Haiku 5.5 (quick classification), served by Amazon Bedrock.

1. **Enable the models in AWS.** In the AWS console, go to Amazon Bedrock → Model access, choose your region, and enable access to `anthropic.claude-sonnet-5-5` and `anthropic.claude-haiku-5-5`.
2. **Get credentials** for an AWS identity that may call Bedrock in that region. Pick one:
   - **AWS CLI profile (recommended):** `aws configure sso` (or `aws configure`), then `aws sso login --profile <name>`.
   - **Bedrock API key:** create one in the Bedrock console.
   - **Instance or container role**, when running on AWS.
3. **Edit the LLM block in `.env`:**

   ```bash
   PHEME_LLM=bedrock
   PHEME_AWS_REGION=us-east-1            # the region where you enabled the models
   PHEME_AWS_PROFILE=my-profile          # your AWS CLI profile; leave empty for the default chain
   PHEME_BEDROCK_API_KEY=                # only if you use a Bedrock API key instead of a profile
   PHEME_MODEL_MAIN=anthropic.claude-sonnet-5-5
   PHEME_MODEL_FAST=anthropic.claude-haiku-5-5
   ```

   Model ids must keep the `anthropic.` prefix; Pheme refuses to start without it.

4. **Restart:** Ctrl-C, then `make dev`.

> **Note:** Pheme reads only `PHEME_*` values from `.env`. Standard AWS variables (`AWS_PROFILE`, `AWS_ACCESS_KEY_ID`, `AWS_BEARER_TOKEN_BEDROCK`, …) are honoured only when they're set in your shell, for example `export AWS_PROFILE=my-profile` before `make dev`. Inside `.env`, use the `PHEME_AWS_*` and `PHEME_BEDROCK_API_KEY` settings instead.

**Optional live check:** this runs one real conversation, costs a few cents, and records the transcript in `docs/live-runs/`.

```bash
cd backend && PHEME_LIVE_LLM=1 uv run pytest tests/orchestrator/test_live_llm.py
```

---

## `.env` reference

Every setting is listed in [.env.example](.env.example) with its default. Some values must match `docker-compose.yml`:

| Settings | Rule |
| --- | --- |
| `PHEME_PG_PORT`, `PHEME_REDIS_PORT`, `PHEME_S3_PORT` | Host ports Docker publishes. Change each one together with the URL that uses it (next section). |
| `PHEME_DATABASE_URL` | The app connects as `pheme_app` (row-level security applies). Keep this user; only change host, port or password. |
| `PHEME_MIGRATION_DATABASE_URL` | Migrations connect as the owner `pheme` (password `pheme`, set in `docker-compose.yml`). |
| `PHEME_APP_DB_PASSWORD` | Password the first migration gives the `pheme_app` role. It must match the password in `PHEME_DATABASE_URL`. To change it after the first run, wipe the data (`docker compose down -v`) or run `ALTER ROLE pheme_app PASSWORD '…'` as `pheme`. |
| `PHEME_S3_ACCESS_KEY`, `PHEME_S3_SECRET_KEY` | Must match the identity in `docker-compose.yml` (`pheme` / `pheme-secret`). |
| `PHEME_S3_BUCKET` | Must be `pheme`, which is the bucket `make deps-up` creates, unless you create another bucket yourself. |
| `PHEME_SECRET_KEY` | Signs sign-in tokens. The dev default is fine locally. Production refuses it and needs 32+ random bytes. |
| `PHEME_ENV` | `dev` locally. `production` enforces real auth and a real secret key. |
| `PHEME_LLM`, `PHEME_AWS_*`, `PHEME_BEDROCK_API_KEY`, `PHEME_MODEL_*` | See [Use the real models (Amazon Bedrock)](#use-the-real-models-amazon-bedrock). |
| `PHEME_OTEL_*`, `PHEME_LANGFUSE_*` | Optional tracing. `none` by default. |
| `PHEME_RATE_LIMIT_PER_MIN` | Requests per workspace per minute. `0` disables the limit. |

### Changing ports

Example: you already run Postgres on 5432, so Pheme's Postgres moves to 5433. Change all three lines:

```bash
PHEME_PG_PORT=5433
PHEME_DATABASE_URL=postgresql+psycopg://pheme_app:pheme_app@localhost:5433/pheme
PHEME_MIGRATION_DATABASE_URL=postgresql+psycopg://pheme:pheme@localhost:5433/pheme
```

Redis and the file store work the same way:
- Redis: `PHEME_REDIS_PORT` and `PHEME_REDIS_URL`
- File store: `PHEME_S3_PORT` and `PHEME_S3_ENDPOINT`

Then run `make deps-down && make dev`.

The API (8000) and web app (5173) ports are fixed in [scripts/dev.sh](scripts/dev.sh).

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Cannot connect to the Docker daemon` | Start Docker Desktop or OrbStack, or run `colima start`. |
| `port is already allocated` / `address already in use` | Another program uses that port. Stop it, or [change the port](#changing-ports). |
| `password authentication failed for user "pheme_app"` | `PHEME_DATABASE_URL` doesn't match the role's password. Use the defaults from `.env.example`, or see `PHEME_APP_DB_PASSWORD` above. |
| `bucket 'pheme' does not exist` | Run `make deps-up`. It recreates the bucket. |
| Upload says "Pheme can't read this kind of file yet" | The file isn't one of the [supported formats](#pheme). Plain-text logs aren't supported yet. |
| `isn't a Bedrock model id` at startup | Add the `anthropic.` prefix to `PHEME_MODEL_MAIN` / `PHEME_MODEL_FAST`. |
| Bedrock `AccessDeniedException` or region errors | Enable model access in that region, and check `PHEME_AWS_REGION` and your credentials (`aws sts get-caller-identity --profile <name>`). |

## Commands

| Command | What it does |
| --- | --- |
| `make dev` | Install, start Docker services, migrate, run API + worker + web app |
| `make deps-up` / `make deps-down` | Start / stop the Docker services |
| `make migrate` | Apply database migrations |
| `make check` | Lint, type checks and tests (backend and frontend) |
| `make e2e` | Browser tests on an isolated database, offline model |
| `make eval` | Evaluation harness; writes `backend/eval/reports/<date>.md` |
| `make demo-data` | Print profiles and chart suggestions for the bundled sample files (no Docker needed) |
| `make help` | List every target |
