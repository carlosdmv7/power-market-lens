# ADR-010 — Ingest Eurostat household prices only when Eurostat publishes

- **Status:** accepted
- **Date:** 2026-11-15

## Context

Phase 2 answers what households pay for electricity across the modelled
countries, and what that price is made of (`CLAUDE.md`, question 3). Eurostat
publishes it in two datasets, free and without a key:

- `nrg_pc_204`: the price per kWh, half-yearly since 2007 for the current
  consumption bands, excluding taxes, excluding VAT, and with every tax
  included;
- `nrg_pc_204_c`: the same price split into components, yearly since 2017:
  energy and supply, network costs, taxes, fees, levies and charges, VAT,
  their sub-items (renewable, capacity, environmental, nuclear taxes) and
  allowances.

Both change a few times a year. ENTSO-E data, by contrast, is revised for
days after delivery, which is why every run re-pulls a trailing week
(ADR-003). Applied to Eurostat, that rule would append the same ~7,000 values
every day: about 2.5 million copies a year of data that changed twice.

## Decision

- **Two typed raw tables**, `raw.eurostat_household_prices` and
  `raw.eurostat_household_price_components`, one column per dimension the
  dataset varies on, like the ENTSO-E tables. The dimensions that never vary
  in our requests (frequency, product, unit) are checked on every response
  and rejected if they change, rather than stored.
- **Scope of the request:** the countries of the modelled zones (ES, PT, FR,
  DE, NL, IT; DE-LU is priced as Germany), in euros, every band and tax
  treatment. A test keeps the country list equal to the `bidding_zones` seed.
- **Append only a new version.** Each response carries Eurostat's `updated`
  time, stored as `source_updated_at`. A load appends the whole dataset when
  that time is newer than the newest one in raw, and nothing otherwise. Raw
  stays append-only and keeps every version Eurostat has published; staging
  will keep the latest.
- **Eurostat's flags are kept** (`status_flag`: `p` provisional, `d`
  definition differs, and so on), never dropped and never turned into nulls.
- **Run by the daily flow**, as two more loads with the same rule as the
  ENTSO-E ones: a failure is recorded and fails the run at the end, but never
  holds back the market data or the dbt build. Checking every day costs two
  small requests and finds a new release within a day.
- `_window_start` is the first period requested (2007-01-01 or 2017-01-01)
  and `_window_end` the load time: the window the request covered.

## Alternatives considered

- **One generic long table** for any Eurostat dataset (dataset code,
  dimension key-value pairs, value). Reusable, but untyped: every query would
  unpack strings, and a renamed dimension would pass silently. Two datasets do
  not justify it.
- **Re-pull and append every day**, as for ENTSO-E. Simple and uniform, but
  raw would grow by millions of identical rows for nothing.
- **A separate, monthly schedule.** One more workflow to watch, and a release
  could wait a month to appear.
- **The `eurostat` Python package.** A new dependency for one HTTP request
  and a JSON-stat decoder of about thirty lines.

## Consequences

- Raw grows only when Eurostat publishes, by about 7,000 rows each time.
- A revision that Eurostat publishes without moving `updated` would be missed.
  Eurostat moves it on every data change; if that ever proves false, the
  check becomes a comparison of contents.
- The freshness check (ADR-007) does not cover Eurostat: its data is months
  old by design. A failed Eurostat load is reported by the run itself.
