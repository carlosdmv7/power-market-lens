# Phase 1 summary

Closed on 2026-11-15. Scope, from the roadmap: all bidding zones, load,
cross-border flows, technology mapping and emission factors, a multi-year
backfill, and the map and zone-mix pages.

## What was built

**Ingestion** (`src/power_market_lens/ingest/`)

- Twelve bidding zones: ES, PT, FR, DE-LU, NL and Italy's seven zones, with
  DE-LU's SDAC auction selected by classification sequence.
- Five ENTSO-E documents: day-ahead prices (A44), actual generation per type
  (A75), actual load and its day-ahead forecast (A65), and physical flows on
  every border a modelled zone owns, both directions (A11).
- Own XML parsers at native resolution (15, 30 or 60 minutes), chunked
  requests with exponential backoff on 429/5xx, the token redacted from every
  error, and an `EntsoeAuthError` that stops the run at the first refused
  request.
- History from 2024-01-01, backfilled through the same workflow as the daily
  load.

**Warehouse and dbt** (`transform/`)

- Raw is append-only; staging keeps the latest load of each interval.
- Marts: `fct_day_ahead_price_hourly`, `fct_day_ahead_price_quarter_hourly`,
  `fct_generation_hourly`, `fct_generation_mix_hourly` (renewable and
  low-carbon share, lifecycle carbon intensity, proxy share),
  `fct_load_hourly` (actual against forecast), `fct_interchange_hourly`
  (imports, exports and net per border), `dim_bidding_zone`.
- Seeds: bidding zones, production types, technology mapping, IPCC AR5
  factors, dated SDAC price limits, and the source gaps ENTSO-E never
  published.
- ADR-003 (time, DST and resolution), ADR-004 (technology mapping and
  emission factors), ADR-006 (known source gaps).

**Orchestration and operations**

- Prefect flows run by a GitHub Actions workflow, six scheduled attempts a
  day with `--skip-if-done`, zero Prefect deployments (ADR-002). Prod runs
  are recorded in Prefect Cloud from 2026-11-15.
- Alerting (ADR-007): step timeouts so a hung run is red, an incident issue
  for any run that does not succeed, and an independent `Data freshness`
  workflow that checks the marts on its own schedule. A runbook
  (`docs/runbook.md`) says what to do for each failure.
- dbt's manifest and run results are kept as an artifact of every prod run.
- `main` is protected: both CI jobs must pass, no force-push.

**App** (`app/`)

- Zone page: price, generation by technology, demand against the TSO's
  forecast and carbon intensity on one time axis, with the week's figures
  stated first. It flags a systematic forecast gap (NL), a high proxy share
  (DE-LU) and stale data.
- Map page: one day's mean price per zone with net cross-border flows as
  arrows, and a ranking.
- One DuckDB cursor per query (safe for concurrent visitors) and a plain
  message when the warehouse cannot be read.

## What was tested

- **pytest, 128 tests, 96% line coverage.** Parsers against recorded ENTSO-E
  responses (both DST days, curve type A03 gaps, DE-LU sequences), the client's
  retry and redaction rules, the flows end to end against a local Prefect
  server with fixtures, the freshness rules, and every page rendered with
  Streamlit's `AppTest`. No test touches the network.
- **dbt, 189 nodes per build.** `unique`/`not_null` on every key, complete
  local days per zone (23, 24 or 25 hours; 92, 96 or 100 quarter-hours),
  contiguous price-limit periods, accepted values, and unit tests for both DST
  changes, finest-resolution selection, the mix weighting and storage, load
  alignment and the interchange point of view.
- **On real data.** Every mart verified against `power_prod`; the last prod
  build (2026-11-15) passed with 0 errors and 2 expected warnings (tomorrow's
  actual load is not measured yet; TSO gaps on a few borders). The auth
  failure and the freshness check were run against the real API and prod;
  the alert job opened its incident issue on a real failed run. Both pages
  were rendered against prod for the README screenshots.

## What is left

From [the review](review-2026-11.md), in the order it recommends:

- **Raw growth.** Each run re-appends the trailing week: about 210,000
  generation rows a day for about 12,000 new points. Proposal 7.1: append
  only rows that changed.
- **dbt contracts and an exposure** for the marts the app reads, now that the
  set of marts is final.
- **dbt-expectations** is installed and unused: use it for volume and
  distribution tests, or remove it (proposal 7.4).
- **Slim CI and dbt docs on GitHub Pages**, roadmap Phase 4; the prod
  manifest they need is now kept.
- **A read-only MotherDuck token** for the app and the freshness check.
- **The scheduler.** GitHub starts the daily run hours late; documented, not
  fixed (proposal 7.3).

Next phase (2): MetricFlow metrics, household price composition from
Eurostat, and the Spanish PVPC breakdown from ESIOS.
