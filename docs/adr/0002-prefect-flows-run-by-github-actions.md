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

## Amendment, 2026-11-15: what running it taught

- **The schedule runs hours late, not minutes.** In the two weeks before
  2026-11-06 the first attempt asked for 12:37 UTC started between 17:20
  and 20:14 UTC, and a second full refresh usually ran around 01:00 UTC.
  Tomorrow's prices therefore reach the app in the evening. Still acceptable
  for an app that explains prices rather than trades on them; an external
  trigger would fix it at the cost of a new service and a token stored
  outside GitHub (review, proposal 7.3).
- **Prefect Cloud reporting is on in prod** since 2026-11-15, when
  `PREFECT_API_KEY` and `PREFECT_API_URL` were added as repository secrets.
  Before that every prod run used a temporary local server and was only in
  the job log. Still zero deployments.
- **A run that does not finish is not reported by GitHub.** A job timeout
  ends "cancelled", which is neither red nor emailed. That, and the alerts
  added for it, are in ADR-007.
- **GitHub disables scheduled workflows after 60 days without repository
  activity.** The pipeline and the freshness check would stop together. The
  app's "Last loaded" notice is the remaining signal; the runbook says how
  to re-enable the schedule.
