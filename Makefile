# Power Market Lens — Makefile
# Dependencies are managed with uv (https://docs.astral.sh/uv/).
# Every target goes through `uv run`, so there is no venv to activate and the
# local commands are character-for-character what CI runs.

.DEFAULT_GOAL := help

# Load .env into commands that talk to MotherDuck, when it exists. CI has no
# .env (secrets arrive as environment variables), and `--env-file` on a missing
# file is an error, so the flag is only added locally.
ENV_FILE := $(if $(wildcard .env),--env-file .env,)
# dbt target for the dbt-* commands: dev (default), ci or prod.
TARGET ?= dev
DBT := uv run $(ENV_FILE) dbt --no-use-colors
DBT_DIRS := --project-dir transform --profiles-dir transform

# ── Setup ──────────────────────────────────────────────────────────────────────
.PHONY: install
install: ## Create .venv from uv.lock + copy .env.example → .env
	uv sync
	@if [ ! -f .env ]; then cp .env.example .env; echo "  created .env"; fi
	uv run pre-commit install
	$(DBT) deps $(DBT_DIRS)
	@echo "✅  Done.  (No uv yet?  curl -LsSf https://astral.sh/uv/install.sh | sh)"

# ── Quality gate ───────────────────────────────────────────────────────────────
# Lint over `.`, never a hand-written list of folders: a list is how a new
# package ends up silently unlinted while CI catches it on the PR.
.PHONY: lint
lint: ## Lint with ruff (same command CI runs)
	uv run ruff check .

.PHONY: lint-sql
lint-sql: ## Lint dbt SQL with sqlfluff (same command CI runs)
	uv run sqlfluff lint transform/models transform/tests

.PHONY: format
format: ## Auto-fix lint + format with ruff and sqlfluff
	uv run ruff check --fix .
	uv run ruff format .
	uv run sqlfluff fix transform/models transform/tests

.PHONY: format-check
format-check: ## Fail if anything is unformatted (same command CI runs)
	uv run ruff format --check .

.PHONY: test
test: ## Run the test suite
	uv run pytest

.PHONY: cov
cov: ## Tests with coverage report
	uv run pytest --cov --cov-report=term-missing

.PHONY: check
check: lint format-check lint-sql test ## Everything CI checks — run before every push

# ── Warehouse ──────────────────────────────────────────────────────────────────
.PHONY: raw-tables
raw-tables: ## Create the raw tables in the APP_ENV database (idempotent)
	uv run $(ENV_FILE) python -m power_market_lens.ingest.warehouse

.PHONY: record-fixtures
record-fixtures: ## Re-record the real ENTSO-E responses the parser tests use (needs ENTSOE_API_KEY)
	uv run $(ENV_FILE) python scripts/record_entsoe_fixtures.py

.PHONY: map-geometry
map-geometry: ## Rebuild app/assets/map_zones.geojson from Natural Earth (ADR-005)
	uv run scripts/build_map_geometry.py

# ── Flows (Prefect) ────────────────────────────────────────────────────────────
# APP_ENV in .env picks the database. Prod is written only by the main-branch
# pipeline, so locally these run against power_dev.
.PHONY: daily
daily: ## Run the daily refresh flow once (trailing window + dbt build)
	uv run $(ENV_FILE) python -m power_market_lens.flows.daily

.PHONY: backfill
backfill: ## Backfill a date range: make backfill START=2026-07-19 END=2026-10-17
	@test -n "$(START)" -a -n "$(END)" || (echo "usage: make backfill START=YYYY-MM-DD END=YYYY-MM-DD" && exit 1)
	uv run $(ENV_FILE) python -m power_market_lens.flows.backfill --start $(START) --end $(END)

# ── dbt ────────────────────────────────────────────────────────────────────────
# Pick the target with TARGET=dev|ci|prod. Prod is written only by the
# main-branch pipeline; running `make dbt-build TARGET=prod` locally breaks
# that rule.
.PHONY: dbt-debug
dbt-debug: ## Check the dbt connection to MotherDuck (TARGET=dev)
	$(DBT) debug $(DBT_DIRS) --target $(TARGET)

.PHONY: dbt-build
dbt-build: ## Seeds, models and tests (TARGET=dev)
	$(DBT) build $(DBT_DIRS) --target $(TARGET)

.PHONY: dbt-docs
dbt-docs: ## Generate and serve dbt docs locally
	$(DBT) docs generate $(DBT_DIRS) --target $(TARGET)
	$(DBT) docs serve $(DBT_DIRS)

# ── App ────────────────────────────────────────────────────────────────────────
.PHONY: app
app: ## Run the Streamlit app (reads WAREHOUSE_PATH, default md:power_prod)
	uv run $(ENV_FILE) streamlit run app/streamlit_app.py

# ── Run ────────────────────────────────────────────────────────────────────────
.PHONY: run
run: ## Run the entrypoint
	uv run python -m power_market_lens

# ── Utilities ──────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove caches and build artefacts (keeps .venv and data/)
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov dist build
	rm -rf transform/target transform/logs
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +

.PHONY: help
help:
	@echo "Power Market Lens — available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
