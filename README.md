# Pheme

Chat-driven agent that turns logs and statistics into dashboards. Build plan: [PHEME_IMPLEMENTATION.md](PHEME_IMPLEMENTATION.md); status: [docs/progress.md](docs/progress.md).

## Setup

Requires Docker (Docker Desktop, OrbStack or Colima), [uv](https://docs.astral.sh/uv/), Node ≥ 24 and pnpm.

```bash
cp .env.example .env
make dev        # deps + migrations + API (:8000) + worker + frontend (:5173)
```

Check it: `curl localhost:8000/health`. Run all checks with `make check`; `make help` lists every target.

### Real models (Amazon Bedrock)

By default the agent runs offline (`PHEME_LLM=fake`). To use Claude on Amazon Bedrock, set in `.env`:

```bash
PHEME_LLM=bedrock
PHEME_AWS_REGION=us-east-1          # or AWS_REGION / your profile's region
PHEME_AWS_PROFILE=my-profile        # optional; empty uses the default AWS credential chain
# PHEME_BEDROCK_API_KEY=...         # optional Bedrock API key instead of SigV4
```

The AWS identity needs access to `anthropic.claude-sonnet-5-5` and `anthropic.claude-haiku-5-5` in that region (override with `PHEME_MODEL_MAIN` / `PHEME_MODEL_FAST`). One recorded live run: `cd backend && PHEME_LIVE_LLM=1 uv run pytest tests/orchestrator/test_live_llm.py`.
