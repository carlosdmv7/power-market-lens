# ADR-011 — Raw stores a point again only when it changed

- **Status:** accepted
- **Date:** 2026-11-16

## Context

Every run re-pulls a trailing week from ENTSO-E so that late and revised data
reaches the warehouse (ADR-003), and every load appended everything it
fetched. ADR-003 accepted the cost: about eight copies of each point, with
staging keeping the latest. It also named the way out if the cost ever
mattered.

It now matters, for compute rather than storage:

- On 2026-11-15 and again at the end of the 2026-11-16 prod build, MotherDuck
  reported the free plan's daily compute limit.
- That build's `run_results.json` adds up to 39 minutes of node time. The
  largest items are the marts and tests that read staging views, which rank
  the whole of raw to find the latest version of each point.
- Raw generation held 17.5 million rows for 3.6 million hourly mart rows.

Measured in prod raw (Spain, 10 November 2026, read on 2026-11-16):

- Every price, generation and load point had six copies, and **not one value
  differed** between them.
- **`document_mrid` and `document_created_at` differed in every copy.** ENTSO-E
  issues a new document id and stamps the request time on each response, so
  these two columns say when we asked, not when the data was revised.

## Decision

- **For the four ENTSO-E tables, a load appends only the rows that are new or
  changed.** A row is appended when its point has no stored version, or when
  its values differ from the point's latest stored version.
- **The point** is the key staging deduplicates on: zone, production type or
  process type where there is one, interval start and resolution. A test keeps
  `warehouse.POINT_KEYS` equal to the `partition by` of each staging model.
- **The values** are every other data column except `document_mrid` and
  `document_created_at`, compared with `is distinct from`. A second test checks
  that no column is left out by accident.
- **One `INSERT … SELECT` per batch**, as before, so a batch still lands whole
  or not at all. The lookup of latest versions is bounded by the batch's zones
  and time span.
- **Raw stays append-only.** Nothing is updated or deleted. A revision is
  stored as a new row, and so is a value that returns to an earlier one,
  because the comparison is with the latest version only.
- **The trailing window, staging and the marts are unchanged.**
- The Eurostat tables keep their own rule (ADR-010): a new version is
  appended whole.

## Alternatives considered

- **Compact raw on a schedule**, keeping the latest copy older than N days.
  Smaller raw, but it breaks "raw is never updated" and deletes data.
- **Leave raw as it is and make staging incremental.** Build time would stay
  flat, but raw would keep growing, and the layer that is simplest today would
  get incremental logic.
- **Compare the document ids as well.** Simple, but useless: they change on
  every request, so every row would count as changed.
- **Upsert in place.** Rejected in ADR-003: it loses the history of revisions.

## Consequences

- **A run appends what is new.** Verified on real data on 2026-11-16, reloading
  Spain's run windows against a copy of prod raw:
  - Prices: 100 rows of about 860 fetched. They are tomorrow's 96 quarter-hours,
    plus 4 at the window's edge that were outside the copy, not outside prod.
  - Generation: 1,702 rows of about 14,000. They are today's 1,679 points, plus
    23 points of 14 November that ENTSO-E published late: exactly what the
    re-pull exists to catch.
  - Revised values: none.
  - Through MotherDuck, into `power_dev`: a second identical load appended 0
    rows.
- **`_loaded_at` changes meaning.** It now means when this version was first
  stored, not when the point was last seen. "When did the pipeline last check"
  is in the run logs and Prefect, not in raw. The freshness heartbeat
  (ADR-007) and the app's "Last loaded" now move when new data arrives, which
  is once a day, with the next day's prices.
- **Each load reads raw before writing:** the latest versions of its zone and
  window. That is a small, bounded read, against the full re-ranking it saves
  on every build.
- **The copies already in raw stay.** Removing them would be deleting data,
  which needs its own decision. Until then, staging still ranks them, so build
  time drops as a share of raw rather than at once. What stops is the growth.
- **Repeating a backfill now appends nothing new**, which makes it cheaper as
  well as safe.
