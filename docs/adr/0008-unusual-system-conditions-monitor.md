# ADR-008 — A monitor of unusual system conditions, not a blackout probability

- **Status:** accepted (2026-11-15); implemented 2026-11-16, ahead of Phase 3 at the owner's request
- **Date:** 2026-11-15

## Context

After Phase 1, the question came up whether the app could estimate the
probability of a blackout, with multivariate statistics such as PCA and
Hotelling T². This ADR answers it with a prototype run on `power_prod`
(`scripts/prototype_system_anomalies.py`, read-only), and proposes what the
technique can honestly deliver instead.

It is outside the four questions in `CLAUDE.md`, so it needs a decision
before any work, and a place in the roadmap.

### Why a blackout probability is not feasible

- **No events to learn from.** The history since 2024 holds one blackout in
  the modelled zones (Iberia, 28 April 2025). A probability cannot be
  estimated, let alone validated, from one case.
- **The causes are not in these data.** A blackout unfolds in seconds:
  voltage control, inertia, protection trips. ENTSO-E's market and
  generation data are hourly or quarter-hourly aggregates and carry none of
  it; the official reports on the Iberian event centre on voltage, which is
  not published at this level.
- **Measured, not assumed.** In the prototype, the thirteen hours published
  on 28 April 2025 before the blackout are unremarkable for Spain: none is
  flagged, the highest T² sits at the reference year's 45th percentile, and
  the highest Q at its 96th, under the 99th-percentile limit. 517 of 683
  evaluation days look at least as normal. These data would not have warned.

A number labelled "blackout probability" on a public page would be read as a
forecast. It would not survive the first question in an interview.

### What the technique does well

Hotelling T² and Q (squared prediction error) are the standard tools of
multivariate statistical process control: they say how far a state lies from
normal, taking into account how the variables move together, and which
variables make it unusual. Applied to the system state per zone and hour,
they answer a question the app does not answer yet: *was this hour unusual,
and why?*

## Prototype

Variables per zone and hour, all from existing marts: load, the load
forecast error, generation by group (wind, solar, nuclear, fossil,
hydro & other), net imports over all borders, and the day-ahead price.

1. **Calendar removed with trailing statistics.** Each value becomes a
   z-score against the previous 28 days at the same local hour, from the
   past only. The std has a floor (10% of the variable's overall std) so
   near-constant cells, like solar at night, cannot dominate.
2. **PCA on the reference year (2024)**, keeping the components that explain
   80% of the variance (4 or 5 of 8 or 9).
3. **T² and Q per hour**, with the reference year's 99th percentiles as
   control limits, and each variable's **contribution** to T².

Results (evaluation 2025-01-01 → 2026-11-15):

| Zone | Hours flagged, reference | Hours flagged, evaluation | What drives the most unusual days |
|---|---|---|---|
| ES | 1.7% | 3.9% | nuclear (a reactor stopping or returning), the load forecast error, price |
| DE-LU | 1.8% | 2.2% | hydro & other (mostly pumped storage), fossil |
| FR | 1.7% | 1.6% | fossil, load |

Two findings shaped the design:

- **A fixed reference year fails.** The first version standardised against
  2024's mean per month, local hour and weekday type. It flagged 61% of the
  hours of 2025–26: the system drifts (more solar each year, the move to
  15-minute products), and dividing by near-zero night-time solar variance
  made T² explode. Trailing statistics and a std floor fixed both.
- **Contributions make the flags readable, and some are real events.**
  Spain, 11 November 2026: the operator's load forecast was 2.4 GW (8%)
  above the actual load through the morning; the contribution analysis
  points straight at the forecast error. Several Spanish days are explained
  by nuclear alone, which matches reactors stopping or restarting.

## Decision

Build a **monitor of unusual system conditions**, per zone and hour, and
never present it as a blackout probability.

- **Computation:** a Python module in `src/power_market_lens/` (numpy and
  pandas only, both already in the stack), unit-tested on synthetic data
  with a known anomaly, run by the Pipeline after `dbt build`.
- **Storage:** the result written to its own schema as one table per run
  (`t2`, `q`, limits, flag, top three contributions per hour), declared as a
  dbt source with `not_null`/`unique` tests, read by the app like a mart.
  The same pattern the Phase 3 forecast will need for its predictions.
- **App:** on the zone page, a band of unusual hours on the shared time axis,
  and for a picked hour the variables that made it unusual. The wording:
  "statistically unusual for this zone, given the last four weeks", with
  the explicit note that it is not a reliability or security assessment.
- **Roadmap:** alongside Phase 3, which needs the same scaffolding (a model
  output table, rolling evaluation).

## Alternatives considered

- **(a) Blackout probability.** Rejected for the reasons above.
- **(b) The monitor as proposed.** Chosen on 2026-11-15.
- **(c) Compute it in the app on the fly.** No warehouse writes and no
  pipeline change; about 25,000 rows per zone is cheap. Rejected: the
  app would hold modelling logic, results would not be tested or stored,
  and the numbers could change between visits.
- **(d) A notebook or an analysis page only.** The cheapest way to show the
  method in an interview, without operating it. A reasonable fallback if
  the monitor does not earn its place in the roadmap.
- **(e) scikit-learn for PCA.** Not needed: PCA is one SVD. Adding a
  dependency for it would be hard to justify.

## Consequences

- The app gains a question it can answer honestly ("was this hour unusual,
  and why?") and an interview-ready piece of multivariate statistics.
- Limits are empirical (99th percentile of 2024), not the textbook F and
  chi-square limits: the residuals are not multivariate normal (heavy tails,
  autocorrelation). Stated in the app and the code.
- Hours are autocorrelated, so a flagged event shows as a run of flagged
  hours, and the false-alarm rate per day is higher than per hour. The app
  should group a run into one event.
- Some flags are known, scheduled events (nuclear refuelling). A later
  version could take planned unavailability from ENTSO-E (document A77/A80)
  to separate planned from unplanned. Out of scope here.
- Nuclear dominates in zones where it is usually flat, because its trailing
  std is small. The floor limits it; per-variable weights may be needed.
- 2024 as the reference year must be revisited once the history is long
  enough for a rolling reference (for example, the previous 12 months).

## Implementation (2026-11-16)

- **Code:** `power_market_lens.monitor.conditions` (the method, pure numpy and
  pandas) and `power_market_lens.monitor.job` (read the marts, write the
  table).
- **When it runs:** the daily and backfill flows run it after `dbt build`. A
  failure is recorded and fails the run at the end, like a failed load; it
  never holds back the freshness check.
- **Output:** `monitor.system_conditions_hourly`, one row per zone and hour:
  - `t2`, `q`, their limits, `unusualness` (the larger of T² and Q over its
    limit, so 1 is the control limit) and `is_unusual`;
  - the statistic that explains the hour;
  - the top three variables, with their z-scores and their share of that
    statistic.
- **Recomputed in full on every run** and replaced in one statement. The
  result is a pure function of the marts, so there is no state to drift. On
  prod: 302,403 hours of features read in 2.5 s, 12 zones scored in 1.3 s.
- **Choices made while building it:**
  - **An hour is explained by the statistic further beyond its limit.** T²
    contributions when T² is, squared residuals when Q is: an hour flagged
    only by Q is still explained.
  - **A variable a zone reports in fewer than half its hours is left out for
    that zone.** The prototype instead dropped every hour it was missing
    from. DE-LU now runs on 8 variables, because Germany has no nuclear.
  - **Episodes are grouped in the app.** Consecutive unusual hours are shown
    as one episode, with its peak and what stood out.
- **Verified on prod's marts, read-only:**
  - The prototype's flag rates hold. ES is 1.7% in the reference year and
    3.9% since 2025; DE-LU 1.8% and 2.2%; FR 1.7% and 1.6%.
  - Spain, 11 November 2026: one episode from 05:00 to 16:00 local. The load
    forecast error leads until 10:00 (z up to +9.9).
  - None of the hours published on 28 April 2025 before the blackout is
    flagged.
- **dbt source and tests, after the monitor (decided 2026-11-17).** The table
  is `source('monitor', 'system_conditions_hourly')`, with these tests:
  - key uniqueness, and `not_null` on the key and the flag;
  - `zone_code` must exist in `dim_bidding_zone`;
  - the accepted statistics, and `unusualness ≥ 0`.

  The app's exposure depends on the source. The tests cannot run inside
  `dbt build`, which comes before the monitor: there they would test the
  previous run's table, or none in CI and in a new database.
  - A default selector (`transform/selectors.yml`) leaves the source and its
    tests out of every command run without a selection.
  - The pipeline runs `dbt test --select source:monitor` right after the
    monitor writes the table. A failure fails the run at the end, like a
    failed load.
  - CI never tests the table: `power_dev` holds days, not the year of history
    the monitor needs.
