# Runbook: the pipeline is red, or the data is stale

What to do when something below fires. The design behind the alerts is in
[ADR-007](adr/0007-alerting-and-the-freshness-watchdog.md).

## How you find out

| Signal | Means |
|---|---|
| GitHub email "Run failed: Pipeline" | A run ended in failure: a load, dbt, source freshness, or a step timeout. |
| An open issue labelled `pipeline-incident` | A Pipeline run did not succeed (including cancelled), or the freshness check failed. Every later failure is a comment on it. |
| Red **Data freshness** badge in the README | A zone has no prices for today, or the newest load is over 36 hours old. |
| "Last loaded" in amber in the app | Same 36-hour rule, seen from the app. |

## First five minutes

```bash
gh run list --workflow pipeline.yml --limit 6     # what ended how, and when
gh run view <run-id> --log-failed | tail -60      # the failing step's last lines
APP_ENV=prod make freshness                       # read-only: what is stale right now
```

Each run also keeps dbt's `run_results.json` as the artifact
`dbt-artifacts-prod` (`gh run download <run-id> -n dbt-artifacts-prod`).

Then match the last error line to a case below.

## Cases

### `EntsoeAuthError: ENTSO-E rejected the security token`

The token expired or was revoked. The run stops at the first request.

1. Log in to the Transparency Platform → My Account Settings → Web API
   Security Token, and generate a new one.
2. Put it in `.env` and in the repository secret:
   `gh secret set ENTSOE_API_KEY`.
3. Backfill the days missed (see [Recovering data](#recovering-data)).

### `N load(s) failed after their retries: ZONE/entity, ...`

Some loads failed; every other load ran and dbt was built on them. Usually
ENTSO-E was slow or down for one document type.

- Nothing to do if the next run is green: every run re-pulls the last 7 days.
- If it lasts longer than 7 days, backfill the missing range for those zones.

### The step timed out (`The action 'Daily refresh' has timed out after 45 minutes`)

A healthy daily run takes 7 to 23 minutes. A timeout means requests were
hanging. Check the log for which load was running, and whether ENTSO-E is
answering at all. The next scheduled attempt retries by itself.

### `dbt build failed on target prod`

Find the failing node in the log (`grep "FAIL "`) or in `run_results.json`.

- **`complete_local_days_*`**: a local day is missing hours. Re-request that
  zone and day from ENTSO-E before deciding anything. If the publisher does
  not have it, add a row to `transform/seeds/known_source_gaps.csv` with the
  reason and the date checked ([ADR-006](adr/0006-known-source-gaps.md)). If
  the publisher has it, the loader lost it: that is a bug, not a seed row.
- **`not_null` on `zone_code` or `psr_name`**: a zone or production type is
  being ingested that the seed does not know. Add it to the seed.
- **Anything else**: the models were built before the tests ran, so the app is
  already showing the data that failed. Treat it as a data bug.

### `Source freshness failed`

The run worked and ENTSO-E delivered nothing newer than 36 hours. Check the
platform itself (transparency.entsoe.eu) before suspecting the loader.

### Data freshness is red and no Pipeline run failed

GitHub dropped or delayed the scheduled runs, or disabled the schedule (it
does after 60 days without repository activity; re-enable it in the Actions
tab). Start a run by hand:

```bash
gh run list --workflow pipeline.yml --limit 3     # make sure none is queued or running
gh workflow run pipeline.yml
```

The Pipeline runs one at a time, and a newly queued run replaces one that is
still waiting, so do not queue a second.

## Recovering data

Prod is written only by the Pipeline workflow on `main`. Never run a flow or
`dbt build` against prod from a laptop.

- **Up to 7 days missed**: a normal run covers it (`gh workflow run pipeline.yml`).
- **More than 7 days**: a backfill for the exact range, inclusive:

  ```bash
  gh workflow run pipeline.yml -f mode=backfill -f start=2026-11-06 -f end=2026-11-14
  ```

  One run handles about a quarter for all zones within its timeout. If a
  slice times out, re-run only the zones it did not reach with
  `-f zones="NL PT"`.

Backfills are safe to repeat: raw is append-only and staging keeps the latest
load of each interval, so loading a range twice changes nothing downstream. A
repeat appends only points that are new or were revised since (ADR-011).

## Closing the incident

Once a run has refreshed the data and `APP_ENV=prod make freshness` passes,
close the `pipeline-incident` issue with one line on the cause and the fix.
It is never closed automatically.
