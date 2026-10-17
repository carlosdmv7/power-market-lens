# ADR-003 — Time, DST and market-resolution conventions

- **Status:** accepted
- **Date:** 2026-10-17

## Context

Electricity data is a minefield of time handling, and the mistakes are silent:

- **Bidding zones keep local time.** Spain delivers on Europe/Madrid time. A
  local day has 23 hours on the last Sunday of March, 25 on the last Sunday of
  October, and 24 otherwise. The local hour 02:00 is missing on one day and
  happens twice on another.
- **Resolution changed mid-history.** SDAC moved from 60-minute to 15-minute
  market time units on 2025-10-01. A backfill spans both, so "one row per
  hour" is false for half of it.
- **Publishers revise data.** ENTSO-E republishes generation figures for days
  after first release, so a value loaded once may later be wrong.
- **Price limits move.** The SDAC harmonised clearing-price limits change
  automatically. The floor went from −500 to −600 EUR/MWh for delivery from
  2026-05-29; the cap is 4000.

## Decision

1. **Store instants in UTC** as `TIMESTAMPTZ`, named `*_utc`. UTC is the key
   everywhere: it is the only clock with no missing or repeated hours.
2. **Derive local time in marts only**, from the zone's IANA time zone in the
   `bidding_zones` seed: `delivery_date_local`, `delivery_hour_local` (0–23)
   and `utc_offset_minutes`. On the October change two rows share
   `delivery_hour_local = 2`, so local columns are attributes, never part of a
   key.
3. **Completeness is counted per local day**: an hourly mart expects 23, 24 or
   25 rows, computed from the time zone and not hard-coded. A dbt unit test
   pins the October change in place.
4. **Preserve native resolution.** Raw and staging carry `resolution_minutes`
   (15 or 60) and are keyed on it. Hourly marts average the quarter-hours into
   the hour (arithmetic mean) and record how many went in. A quarter-hour mart
   is kept where 15-minute data exists.
5. **Raw is append-only.** Every load adds rows with `_loaded_at`, `_source`,
   `_window_start` and `_window_end`, and nothing is updated in place.
   Staging deduplicates by the natural key, keeping the latest `_loaded_at`.
   Every run re-pulls a trailing window (default 7 days) to pick up revisions.
6. **Validate prices against dated limits.** A `sdac_price_limits` seed with
   `valid_from` and `valid_to` is joined on delivery date. Out-of-range prices
   are flagged in a column and reported by a warning test, never dropped.

## Alternatives considered

- **Store local timestamps** (as many CSV exports do): ambiguous at the October
  change and impossible to join across zones.
- **Resample everything to hourly at ingestion**: simpler, but it destroys the
  15-minute data before anyone can look at it, and it hides the resolution
  break.
- **Upsert raw in place**: smaller tables, but it loses the history of
  revisions and makes a bad load impossible to audit or roll back.
- **A fixed −500/4000 sanity range**: would already flag valid prices since
  May 2026.

## Consequences

- Every timestamp question has one answer: compare in UTC, display in local
  time.
- Raw stores each point about eight times (the first load plus seven daily
  re-pulls). Columnar compression keeps this cheap at this volume, but it is
  the main driver of storage on the MotherDuck free plan (ADR-001). If it
  becomes a problem, the loader can append only the points whose value changed,
  without giving up append-only.
- Staging is a little more expensive (a window function per source), in
  exchange for full auditability.
- Hourly averages of quarter-hours are a convention, not a market price. The
  app says so wherever hourly prices are shown after 2025-10-01.
