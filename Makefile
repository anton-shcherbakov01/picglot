# LingoImage AI — top level task runner.
# Windows users without GNU make: use the equivalent npm scripts
#   npm run setup | dev | lint | typecheck | test | build ...
# or the PowerShell shim: ./scripts/make.ps1 <target>

SHELL := /bin/bash
.DEFAULT_GOAL := help

PY ?= python3
VENV := .venv
VENV_BIN := $(VENV)/bin
COMPOSE := docker compose
API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: help setup setup-py setup-web dev dev-api dev-web dev-worker lint lint-py lint-web \
        typecheck typecheck-py typecheck-web test test-py test-web test-e2e build build-web \
        up down logs ps migrate migration seed health clean fmt docker-build \
        security-scan sbom load-test backup restore

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: setup-py setup-web ## Install all dependencies and create .env
	@test -f .env || (cp .env.example .env && echo "-> created .env from .env.example")
	@echo "Setup complete. Next: make up && make migrate && make seed"

setup-py: ## Create the Python virtualenv and install the API/worker package
	$(PY) -m venv $(VENV)
	$(VENV_BIN)/python -m pip install --upgrade pip wheel
	$(VENV_BIN)/pip install -e "$(API_DIR)[dev,ocr]"

setup-web: ## Install Node dependencies
	npm install

dev: ## Run the full local stack (infra in Docker, app processes locally)
	$(COMPOSE) up -d postgres redis minio minio-init mailpit
	npm run dev

dev-api: ## Run only the API with autoreload
	$(VENV_BIN)/uvicorn lingoimage.main:app --reload --host 0.0.0.0 --port 8000

dev-web: ## Run only the Next.js dev server
	npm run dev --workspace @lingoimage/web

dev-worker: ## Run a local CPU worker
	$(VENV_BIN)/celery -A lingoimage.workers.celery_app worker -Q cpu,export,notifications -l info

lint: lint-py lint-web ## Lint everything

lint-py:
	$(VENV_BIN)/ruff check $(API_DIR)
	$(VENV_BIN)/ruff format --check $(API_DIR)

lint-web:
	npm run lint

typecheck: typecheck-py typecheck-web ## Type-check everything

typecheck-py:
	$(VENV_BIN)/mypy $(API_DIR)/lingoimage

typecheck-web:
	npm run typecheck

fmt: ## Auto-format everything
	$(VENV_BIN)/ruff format $(API_DIR)
	$(VENV_BIN)/ruff check --fix $(API_DIR)
	npm run format

test: test-py test-web ## Run unit + integration tests

test-py:
	$(VENV_BIN)/pytest $(API_DIR)/tests -v

test-web:
	npm run test

test-e2e: ## Run Playwright end-to-end tests
	npm run test:e2e

build: build-web ## Production build
	$(VENV_BIN)/python -m compileall -q $(API_DIR)/lingoimage

build-web:
	npm run build

up: ## Start the full Docker stack
	$(COMPOSE) up -d --build

down: ## Stop the Docker stack
	$(COMPOSE) down

logs: ## Tail Docker logs
	$(COMPOSE) logs -f --tail=200

ps: ## Show container status
	$(COMPOSE) ps

migrate: ## Apply database migrations
	$(VENV_BIN)/alembic -c $(API_DIR)/alembic.ini upgrade head

migration: ## Create a new migration: make migration m="add table"
	$(VENV_BIN)/alembic -c $(API_DIR)/alembic.ini revision --autogenerate -m "$(m)"

seed: ## Load plans, feature flags, SEO content and demo data
	$(VENV_BIN)/python -m lingoimage.cli seed

health: ## Check that every service is reachable
	$(VENV_BIN)/python -m lingoimage.cli health

docker-build: ## Build all production images
	$(COMPOSE) -f docker-compose.production.yml build

security-scan: ## Dependency + secret scanning
	$(VENV_BIN)/pip-audit -r $(API_DIR)/requirements.lock || true
	npm audit --audit-level=high || true

sbom: ## Generate a CycloneDX SBOM
	$(VENV_BIN)/cyclonedx-py environment -o sbom-python.json
	npx --yes @cyclonedx/cyclonedx-npm --output-file sbom-node.json

load-test: ## Run k6 load tests
	k6 run tests/load/upload.js

backup: ## Back up Postgres + MinIO
	./scripts/backup.sh

restore: ## Restore from the newest backup
	./scripts/restore.sh

clean: ## Remove build artefacts and caches
	rm -rf .venv node_modules apps/web/.next .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
