# Pheme

Chat-driven agent that turns logs and statistics into dashboards. Build plan: [PHEME_IMPLEMENTATION.md](PHEME_IMPLEMENTATION.md); status: [docs/progress.md](docs/progress.md).

## Setup

Requires Docker (Docker Desktop, OrbStack or Colima), [uv](https://docs.astral.sh/uv/), Node ≥ 24 and pnpm.

```bash
cp .env.example .env
make dev        # deps + migrations + API (:8000) + worker + frontend (:5173)
```

Check it: `curl localhost:8000/health`. Run all checks with `make check`; `make help` lists every target.
