# Power Market Lens

## Goal
Portfolio analytics platform explaining day-ahead electricity prices across European bidding zones (ES, PT, FR, DE-LU, NL, IT zones): generation mix, price drivers, household price composition, and a leakage-safe day-ahead price forecast for Spain. Foundations matter more than features.

## Questions the project answers
1. Generation mix per bidding zone (by technology), renewable share and carbon intensity (production-based).
2. Drivers of day-ahead prices (load, wind/solar, cross-border flows, gas) as seen by the forecasting model (SHAP). Framed as model drivers, not causal proof.
3. Household price composition across countries (energy, network, taxes/levies, VAT) from Eurostat, plus the daily Spanish PVPC breakdown from ESIOS. National policies appear only as chart annotations.
4. A price map with cross-border flows, and a day-ahead price forecast for Spain with prediction intervals vs actuals.

## Non-goals for v1
Intraday or real-time markets, modelling national subsidies, any AI assistant (see section 4 of the kickoff).

## Working mode
- I am building this to learn and to defend every line in interviews. Explain non-trivial decisions briefly. For dbt models, propose the SQL and wait for my review before moving on.
- Work only on the current phase. At the end of each phase, summarize what was built, what was tested, and what is left.
- Ask before adding any dependency, service or tool not listed below.
- Record significant decisions as short ADRs in `docs/adr/` (context, decision, consequences).
- Code, comments and docs in English.

## Stack (fixed)
| Layer | Choice |
|---|---|
| Language | Python 3.13 (if a key library fails to install, propose falling back to 3.12 via an ADR) |
| Tooling | uv, ruff, sqlfluff, pre-commit, pytest, Pydantic |
| Sources | ENTSO-E Transparency Platform (entsoe-py), REE ESIOS, Open-Meteo (including the Historical Forecast API), Eurostat; gas/CO2 via yfinance only if reliable |
| Warehouse | MotherDuck free plan, databases `power_dev` and `power_prod` |
| Transformations | dbt-duckdb: staging → intermediate → marts |
| Quality | dbt generic and singular tests, dbt unit tests, dbt-expectations, source freshness |
| Semantic layer | MetricFlow for metric definitions; the app reads materialized marts |
| Orchestration | Prefect Cloud free tier for flow logic and observability, executed by a scheduled GitHub Actions workflow |
| ML | LightGBM + SHAP; prediction intervals via quantile regression or conformal prediction |
| App | Streamlit Community Cloud (public app) |
| CI | GitHub Actions: ruff, sqlfluff, pytest, dbt build (slim CI with deferral to the prod manifest) |
| Docs | README, ADRs, dbt docs on GitHub Pages, case study with screenshots and a GIF |

## Hard rules
- Zero cost: no paid APIs or services.
- Secrets only via environment variables; maintain `.env.example`; never commit secrets.
- No network calls in tests: use recorded fixtures.
- Prod is written only by the main-branch pipeline.
- Prefect's free tier has a small deployment quota shared with my other projects: keep deployments to a minimum.

## Data conventions
- Timestamps stored in UTC (`TIMESTAMPTZ`). Marts also expose the zone's local delivery date and hour.
- DST: local days have 23, 24 or 25 hours. Completeness tests must account for this.
- Resolution: SDAC moved to 15-minute market time units on 2025-10-01. Preserve native resolution in raw and staging (`resolution_minutes` column). Hourly marts aggregate quarter-hours as the mean price; keep a quarter-hour mart where data exists.
- Entity: bidding zone identified by EIC code, mapped to country via a seed. Italy has several zones; DE-LU is a single zone.
- Prices can be negative. Sanity range: -500 to 4000 EUR/MWh (SDAC harmonised limits). Flag values outside the range; never drop them silently.
- Raw is append-only and never updated, with metadata columns `_loaded_at`, `_source`, `_window_start`, `_window_end`. Staging deduplicates keeping the latest `_loaded_at`. Each run re-pulls a trailing window (default 7 days) to capture publisher revisions.
- Respect API rate limits; backfills run in chunks with retries and backoff.
- Naming: `stg_<source>__<entity>`, `int_<entity>__<verb>`, `fct_<entity>`, `dim_<entity>`; snake_case everywhere.
- Every model and column has a description; keys have `unique` and `not_null` tests.

## Forecasting rules
- Only features available before the day-ahead gate closure (12:00 CET on D-1): day-ahead load and renewable forecasts, and weather forecasts (Open-Meteo Historical Forecast API for backtests). Never use realized values of the target day.
- Evaluate with rolling-origin backtests against a naive baseline (same hour last week). Report MAE overall and by hour and season, plus interval coverage.
- SHAP explains the model, not causality; word it that way in the app.

## Key decisions (initial ADRs)
- ADR-001: MotherDuck over BigQuery. Google Cloud required a EUR 25 prepayment; MotherDuck is free and already familiar.
- ADR-002: Prefect executed via GitHub Actions. Free tier, already familiar, Prefect acquired Dagster in July 2026; execution on GitHub Actions avoids depending on the free tier's managed compute.
- ADR-003: Time, DST and resolution conventions (as above).

## Commands

- `make install` — sync deps with uv
- `make check` — lint + format check + tests (the CI gate). Run it before saying a change is done.
- `make format` — auto-fix
- `make run` — run the entrypoint

## Repo conventions (from the template)

- Python via `uv run`, never a bare `python` / `pip`. Add deps with `uv add`.
- Ruff is the only Python linter and formatter (config in `pyproject.toml`).
- Code in `src/power_market_lens/`, tests in `tests/` mirroring it.
- Conventional commits; branch from fresh `main`; PR + green CI to merge.
- Decisions with trade-offs get an ADR in `docs/adr/`.
- Verify changes by running them, not only by reading code or unit tests.
