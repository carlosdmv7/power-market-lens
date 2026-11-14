# ADR-007 — Alert on runs that do not finish, and watch freshness independently

- **Status:** accepted
- **Date:** 2026-11-14

## Context

From 2026-11-06 to 2026-11-14 `power_prod` received no data and nothing said
so. The ENTSO-E token had stopped working; every load failed, was retried, and
each run was cut at the job's 60-minute timeout. Three things made the outage
silent:

1. **A job that times out ends `cancelled`, not `failure`.** GitHub shows a
   cancelled run in grey and sends no email. Checked on a throwaway workflow:
   a job with `timeout-minutes: 1` ends `cancelled`, and the run stays
   `cancelled` even when a later job fails. A *step* with `timeout-minutes: 1`
   fails its job, and the run ends `failure`.
2. **Every check lived inside the run.** `dbt source freshness` and the dbt
   tests only execute when the flow reaches them. A run that never gets there
   checks nothing.
3. **Nothing looked at the result.** The app showed "Last loaded" in amber,
   which only helps someone who opens the app.

The rejected-token case itself now fails in seconds (`EntsoeAuthError`), but
the next cause of a hung or cancelled run will be a different one.

## Decision

Three layers, all inside the fixed stack (GitHub Actions, no new service):

1. **Step timeouts.** The two working steps of the Pipeline workflow carry
   their own `timeout-minutes` (45 daily, 55 backfill), below the job's 60. A
   hung run now ends red and triggers GitHub's failure email.
2. **An `alert` job** in the Pipeline workflow runs whenever the main job ends
   in anything but success (`always()` lets it start after a cancellation) and
   opens an issue labelled `pipeline-incident`, or comments on the open one.
   This covers manual cancellations and the job-level backstop, and leaves a
   durable record that an email does not.
3. **A freshness watchdog**: the `Data freshness` workflow, on its own
   schedule, runs `python -m power_market_lens.freshness` against `power_prod`
   (read-only queries). It fails and reports to the same incident issue when a
   zone has no prices for today's delivery day, or the newest load is older
   than 36 hours. It judges the marts, so it does not matter why they are
   stale: no run, a cancelled run, a run that loaded nothing.

The incident issue is closed by a person. A later green run does not close it:
a scheduled attempt that finds the day's prices already built exits green in
seconds and proves nothing about what broke.

## Alternatives considered

- **A final job that fails when the main job was cancelled.** Does not work:
  the run's conclusion stays `cancelled` (point 1 above).
- **`dbt source freshness` as the watchdog.** It measures the newest delivery
  interval against `now` with a single threshold, and day-ahead prices are
  normally "fresh into the future" by up to 36 hours, so a threshold that
  never false-alarms only fires two to three days into an outage. The Python
  rule states the business expectation directly (today's prices exist) and
  fires on the first night.
- **"Tomorrow's prices by this evening."** Tighter, but GitHub starts the
  12:37 UTC schedule five to seven hours late on most days (first runs between
  17:20 and 20:14 UTC in the two weeks before the outage). A deadline tight
  enough to be useful would alert on healthy days.
- **An external dead-man's switch** (a service that expects a ping after each
  successful run and alerts when it is missing). It is the only option that
  survives GitHub Actions itself being down or the repository's schedules
  being disabled. Not adopted: it is a new service outside the stack in
  CLAUDE.md. Recorded here as the known gap.

## Consequences

- An outage like November's is reported on its first night instead of never:
  red `Data freshness` run, failure email, and an open issue.
- The watchdog shares GitHub's scheduler with the pipeline. If GitHub disables
  the repository's scheduled workflows (it does after 60 days without repo
  activity) both stop together, silently. The app's "Last loaded" indicator is
  the remaining signal in that case.
- Detection is bounded by the rule, not by the cause: a failure at midday is
  reported after Brussels midnight, up to about 18 hours later given GitHub's
  delays.
- A manual cancellation of a Pipeline run opens an incident too. That is
  accepted noise: it is rare and it is one comment.
- While an outage lasts, each failed run and each watchdog run adds a comment
  to the open issue (a handful a day).
- The watchdog job needs `issues: write` and reads `power_prod` with the same
  MotherDuck token as the pipeline; a read-only token would be the tighter
  choice once one is created.
