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
