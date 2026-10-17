
# Power Market Lens

[![CI](https://github.com/carlosdmv7/power-market-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/carlosdmv7/power-market-lens/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Why does electricity cost what it costs in Europe? An analytics platform that
explains day-ahead prices across bidding zones: generation mix, price drivers,
household price composition, and a leakage-safe day-ahead price forecast for
Spain.**

> 🚧 Work in progress: Phase 0 (foundations and a Spain-only vertical slice).

**Live demo:** TODO link · **Write-up:** TODO link

TODO — screenshot or GIF (`docs/img/`).

---

## Why

TODO — the problem in two or three sentences. Why existing tools don't answer it.

## What it does

- TODO — capability, with a concrete number
- TODO
- TODO

## Architecture

```text
TODO   source ──► ingest ──► warehouse ──► transform ──► app
```

| Layer | Tool | Why this one |
|---|---|---|
| TODO | TODO | TODO — link the ADR in `docs/adr/` |

## Quickstart

```bash
make install     # uv sync, .env from .env.example, pre-commit hooks, dbt deps
make check       # ruff + sqlfluff + pytest — the same gate CI runs
make dbt-debug   # needs MOTHERDUCK_TOKEN in .env
```

Requires [uv](https://docs.astral.sh/uv/). `make help` lists every command.

## Project layout

```text
src/power_market_lens/   the package: config, ingestion, Prefect flows
transform/               dbt project (staging → intermediate → marts)
tests/                   pytest suite — mirrors src/
docs/adr/                architecture decision records
```

## Decisions

Non-obvious choices are written up as ADRs in [`docs/adr/`](docs/adr/) —
context, the decision, and what it cost.

## Limitations & next steps

- TODO — be honest; it reads better than pretending there are none.

## License

MIT — see [LICENSE](LICENSE).
