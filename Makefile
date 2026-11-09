SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help install deps-up deps-down migrate dev check check-backend check-frontend \
        test-slow e2e types eval demo-data fmt

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	cd backend && uv sync --frozen
	cd frontend && pnpm install --frozen-lockfile

deps-up: ## Start postgres, redis and the S3 store, and create the bucket
	docker compose up -d --wait postgres redis seaweedfs
	docker compose run --rm s3-init

deps-down: ## Stop the dependencies (keeps volumes)
	docker compose down

migrate: ## Apply database migrations
	cd backend && uv run alembic upgrade head

dev: install deps-up migrate ## Run deps, API, worker and frontend (Ctrl-C stops all)
	./scripts/dev.sh

check: deps-up check-backend check-frontend ## ruff, mypy, pytest, eslint, tsc, vitest

check-backend:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests
	cd backend && uv run pytest -m "not slow"

check-frontend:
	cd frontend && pnpm lint && pnpm typecheck && pnpm test

test-slow: deps-up ## Slow backend tests (e.g. the 200 MB ingest)
	cd backend && uv run pytest -m slow

fmt: ## Auto-format and auto-fix
	cd backend && uv run ruff check --fix . && uv run ruff format .

e2e: ## Playwright end-to-end tests
	@echo "make e2e: placeholder until Phase 4 (M10)"

SCHEMAS := backend/app/models/schemas
GENERATED := frontend/src/api/generated

types: ## Regenerate frontend types from the spec and plan JSON Schemas
	cd backend && uv run python -m app.models.export_schemas
	mkdir -p $(GENERATED)
	cd frontend && pnpm exec json2ts -i ../$(SCHEMAS)/dashboard_spec.schema.json -o src/api/generated/dashboard-spec.ts --unreachableDefinitions --additionalProperties false
	cd frontend && pnpm exec json2ts -i ../$(SCHEMAS)/draft_plan.schema.json -o src/api/generated/draft-plan.ts --unreachableDefinitions --additionalProperties false

eval: ## Run the evaluation harness
	@echo "make eval: placeholder until Phase 5 (M14)"

demo-data: ## Print profiles, recommendations and verdicts for the fixtures
	@echo "make demo-data: placeholder until Phase 2"
