# Shortcuts for common tasks. Run `make help` to list them.
.PHONY: help install infra-up infra-down infra-logs migrate dev-backend dev-frontend up down \
	test test-backend test-frontend lint lint-backend lint-frontend

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

install: ## Install backend + frontend dependencies
	cd backend && uv sync
	cd frontend && npm install

infra-up: ## Start Postgres, Floci (S3) and ChromaDB in Docker
	docker compose up -d --wait postgres floci chroma

infra-down: ## Stop the infrastructure (data is kept in Docker volumes)
	docker compose down

infra-logs: ## Follow infrastructure logs
	docker compose logs -f postgres floci chroma

migrate: ## Apply database migrations (alembic upgrade head)
	cd backend && uv run alembic upgrade head

dev-backend: ## Run FastAPI with auto-reload on :8000
	cd backend && uv run fastapi dev app/main.py

dev-frontend: ## Run Next.js dev server on :3000
	cd frontend && npm run dev

up: ## Run EVERYTHING in Docker (infra + migrations + backend + frontend)
	docker compose --profile app up -d --build --wait

down: ## Stop everything started by `make up`
	docker compose --profile app down

test: test-backend test-frontend ## Run all tests (no Docker needed)

# Extra pytest flags, e.g. `make test-backend PYTEST_ARGS="--cov -x"`. CI uses it for coverage.
PYTEST_ARGS ?=

test-backend: ## Run backend tests (pytest)
	cd backend && uv run pytest $(PYTEST_ARGS)

test-frontend: ## Run frontend tests (vitest)
	cd frontend && npm test

lint: lint-backend lint-frontend ## Lint + type-check everything

lint-backend: ## ruff check, ruff format --check, mypy
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app scripts tests

lint-frontend: ## eslint + tsc
	cd frontend && npm run lint && npm run typecheck
