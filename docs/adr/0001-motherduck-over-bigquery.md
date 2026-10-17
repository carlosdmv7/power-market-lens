# ADR-001 — Use MotherDuck as the warehouse, not BigQuery

- **Status:** accepted
- **Date:** 2026-10-17

## Context

The project has a hard zero-cost rule and needs a cloud warehouse that three
things can reach: the GitHub Actions pipeline that writes it, dbt that
transforms it, and a public Streamlit app that reads it. The data is small. A
2–3 year backfill of every bidding zone at 15-minute resolution is tens of
millions of rows, a size any columnar engine handles easily.

BigQuery was the first candidate, but setting up a Google Cloud billing account
required a EUR 25 prepayment. The BigQuery sandbox needs no billing account,
but it expires tables after 60 days, which rules it out for a multi-year history.

## Decision

Use **MotherDuck** on the free plan, with one database per environment:
`power_dev` for local work and pull-request CI, and `power_prod`, written only
by the main-branch pipeline.

## Alternatives considered

- **BigQuery**: needs the prepayment, or the sandbox and its 60-day table
  expiry.
- **A DuckDB file in the repo or in object storage**: free, but the app and the
  pipeline would have to share a file. Concurrent writes, versioning a binary in
  git, or paying for storage all bring back the problems a warehouse solves.
- **Postgres on a free tier (Supabase, Neon)**: a row store for an analytical
  workload, with small storage caps and pausing on inactivity.

## Consequences

- Development and the cloud use the same engine: DuckDB SQL runs identically on
  a laptop and in MotherDuck, and `dbt-duckdb` targets both.
- I already know it from Spanish Housing Radar, so no setup time is spent on
  learning a warehouse.
- One token (`MOTHERDUCK_TOKEN`) covers the loader, dbt and the app, which keeps
  secrets handling simple. The cost is that the token is not scoped: CI's token
  could write `power_prod`. The rule "prod is written only by the main-branch
  pipeline" is enforced by the code and the workflows, not by permissions.
- The free plan has storage and compute limits. Append-only raw with a trailing
  re-pull window multiplies stored rows (see ADR-003), so storage is the number
  to watch.
- Vendor risk: MotherDuck is a young company. The exit path is cheap, because
  everything is DuckDB SQL and dbt and the data can be exported to Parquet.
