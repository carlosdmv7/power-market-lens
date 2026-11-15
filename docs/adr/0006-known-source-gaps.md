# ADR-006 — Exempt verified source gaps from the completeness test

- **Status:** accepted
- **Date:** 2026-10-26

## Context

`complete_local_days` (ADR-003) fails when any local delivery day between a
zone's first and last day lacks some of its 23, 24 or 25 hours. After the prod
backfill (2024-01-01 → 2026-10-14) it passed on prices and failed on six days
of `fct_generation_hourly`. Re-requesting those days from ENTSO-E (A75) on
2026-10-26 returned the same gaps, hour for hour:

- ES, 2025-04-28 from 13:00 and all of 2025-04-29: the Iberian blackout
  (12:33 CEST on 28 April 2025);
- FR, four days in 2025-10 → 2026-01 with one to 23 hours unpublished.

The data does not exist upstream, so no reload can fix it, and the daily
pipeline stayed red on it.

## Decision

A seed, `known_source_gaps`, lists each (model, zone, local day) the publisher
never released, with the missing hours and the reason. `complete_local_days`
skips those rows for the model it tests and fails on any other incomplete day.
A row is added only after re-requesting the day from the source.

Rejected: lowering the test's severity to `warn`. It turns the pipeline green
in one line, but a new real gap would then only be a log warning nobody reads.

## Consequences

- The pipeline goes green again, and a new gap still fails it.
- Each exception is documented with its cause, which is also material for the
  app (the blackout is worth an annotation).
- Someone has to verify and add a row when ENTSO-E skips data again; that is
  the intended friction.
- If ENTSO-E later fills a gap, its row becomes a no-op until removed.

## Amendment, 2026-11-14: recent days warn, settled days fail

On 2026-11-14 the test failed on one hour of French generation from the day
before (13 November, local hour 19), absent at ENTSO-E when re-requested. The
decision above would have it added to the seed at once, but a day-old gap is
not yet a verified one: the publisher may still release it, and every run
re-pulls the last seven days.

A failing test also costs more than a red run. `dbt build` skips every model
downstream of a failed test, so a late hour would stop any mart built on
`fct_generation_hourly` from refreshing.

For `fct_generation_hourly` the test now runs twice: days inside the trailing
window warn, days older than it fail. The seed stays the way to record a gap
that outlived the window. Prices keep the single strict test: they are set a
day ahead, and a missing price hour is never a matter of late publication.

Trade-off: a real gap in measured data now turns the pipeline red seven days
after it appears, not the next day. The warning is in the log and in
`run_results.json` during that week.
