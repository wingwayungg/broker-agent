# Local development. Expects the Python venv at ./venv (see README) and pnpm.
PY := $(CURDIR)/venv/bin

.PHONY: dev api web test lint build

# The API on :8000 and the web app on http://localhost:3000, together.
dev:
	$(MAKE) -j2 api web

api:
	cd backend && $(PY)/uvicorn api.main:app --reload --port 8000

web:
	pnpm -C frontend dev

test:
	cd backend && $(PY)/python -m pytest
	pnpm -C frontend test

lint:
	cd backend && $(PY)/ruff check . && $(PY)/ruff format --check . && $(PY)/pyright
	pnpm -C frontend lint
	pnpm -C frontend typecheck

build:
	pnpm -C frontend build
