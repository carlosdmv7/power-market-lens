# Power Market Lens — Makefile
# Dependencies are managed with uv (https://docs.astral.sh/uv/).
# Every target goes through `uv run`, so there is no venv to activate and the
# local commands are character-for-character what CI runs.

.DEFAULT_GOAL := help

# ── Setup ──────────────────────────────────────────────────────────────────────
.PHONY: install
install: ## Create .venv from uv.lock + copy .env.example → .env
	uv sync
	@if [ ! -f .env ]; then cp .env.example .env; echo "  created .env"; fi
	@echo "✅  Done.  (No uv yet?  curl -LsSf https://astral.sh/uv/install.sh | sh)"

# ── Quality gate ───────────────────────────────────────────────────────────────
# Lint over `.`, never a hand-written list of folders: a list is how a new
# package ends up silently unlinted while CI catches it on the PR.
.PHONY: lint
lint: ## Lint with ruff (same command CI runs)
	uv run ruff check .

.PHONY: format
format: ## Auto-fix lint + format with ruff
	uv run ruff check --fix .
	uv run ruff format .

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
check: lint format-check test ## Everything CI checks — run before every push

# ── Run ────────────────────────────────────────────────────────────────────────
.PHONY: run
run: ## Run the entrypoint
	uv run python -m power_market_lens

# ── Utilities ──────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove caches and build artefacts (keeps .venv and data/)
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +

.PHONY: help
help:
	@echo "Power Market Lens — available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
