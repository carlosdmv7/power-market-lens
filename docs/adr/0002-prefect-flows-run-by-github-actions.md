# ADR-002 — Run Prefect flows from GitHub Actions, with no Prefect deployments

- **Status:** accepted
- **Date:** 2026-10-17

## Context

The pipeline needs a daily schedule, retries with backoff against rate-limited
APIs, a backfill over an arbitrary date range, and run history I can inspect.
Everything must cost nothing.

Prefect is the orchestrator I use at work, and it is on a solid footing: in
July 2026 Prefect announced it had agreed to acquire Dagster Labs, its main
open-source rival. But Prefect Cloud's free tier constrains how it can be used:

- It has no hybrid or push work pools, so Prefect cannot dispatch runs to my own
  machines. Its managed (serverless) compute is a small monthly allowance that
  my other projects already use most of.
- Deployments are capped at 5 per workspace, shared with my other projects. Two
  are already taken.

## Decision

Prefect owns the **flow logic and observability**; GitHub Actions owns
**scheduling and compute**.

- A scheduled workflow on `main` runs the daily flow as a plain Python process
  (`python -m power_market_lens.flows.daily`). With `PREFECT_API_KEY` set, the
  run, its tasks, retries and logs all appear in Prefect Cloud. A flow run does
  not need a deployment.
- The backfill is the same kind of process, started by a `workflow_dispatch`
  with `start` and `end` inputs.
- **This project registers zero Prefect deployments.**

## Alternatives considered

- **A served deployment polled hourly by GitHub Actions**, as in Spanish
  Housing Radar. Runs can then be started from the Prefect UI, but the project
  takes one of the five shared deployment slots, and an hourly poller adds
  moving parts (paused schedules, *Late* runs) for a job that runs once a day.
- **Prefect serverless work pools**: the free minutes are already committed
  elsewhere.
- **GitHub Actions alone, without Prefect**: no task-level retries, state or run
  history beyond raw job logs. These are exactly what Prefect adds.
- **Dagster**: its asset model fits dbt well, but it would be a second
  orchestrator to learn and operate, for no gain at this scale.

## Consequences

- No quota is consumed, and there is no runner process to keep alive or to find
  paused.
- The schedule lives in the workflow's `cron`, not in Prefect. Runs cannot be
  started from the Prefect UI; manual runs go through `workflow_dispatch`.
- GitHub can delay scheduled jobs, sometimes by hours. Acceptable for a daily
  refresh. Day-ahead results publish around 13:00 CET, so the cron sits after
  that with margin.
- If Prefect Cloud is unreachable, the flow still runs and only loses its UI
  record. Orchestration is not a single point of failure.
- Prod is written only by this workflow on `main`. The workflow refuses to run
  on any other ref.
