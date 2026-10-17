
# Power Market Lens

[![CI](https://github.com/carlosdmv7/power-market-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/carlosdmv7/power-market-lens/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%20|%203.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**TODO — one sentence a recruiter understands: what it does, for whom, and the
headline number.**

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
make install   # uv sync + .env from .env.example
make check     # lint + format check + tests — the same gate CI runs
make run
```

Requires [uv](https://docs.astral.sh/uv/). `make help` lists every command.

## Project layout

```text
src/power_market_lens/   the package (application code)
tests/             pytest suite — mirrors src/
docs/adr/          architecture decision records
scripts/           one-off and maintenance scripts (create when needed)
```

## Decisions

Non-obvious choices are written up as ADRs in [`docs/adr/`](docs/adr/) —
context, the decision, and what it cost.

## Limitations & next steps

- TODO — be honest; it reads better than pretending there are none.

## License

MIT — see [LICENSE](LICENSE).
