"""Is the warehouse fresh? A check that does not depend on the pipeline running.

    python -m power_market_lens.freshness        # exits 1 and says why when stale

The pipeline checks its own sources (`dbt source freshness`), but only when it
gets that far. A run that is cancelled, times out, or never starts checks
nothing, which is how prod stayed stale for eight days in November 2026
(ADR-007). This module looks at the result instead of the process: the marts
the app reads. It is run by its own scheduled workflow, and it only reads.

Two rules, both on `marts.fct_day_ahead_price_hourly`:

- **Coverage.** Every zone has prices for today's delivery day (Brussels
  time, the clock SDAC runs on). They were auctioned yesterday around 12:45,
  so by the time the day starts they have had eleven hours to arrive. The rule
  is deliberately not "tomorrow's prices by this evening": GitHub starts the
  12:37 UTC schedule five to seven hours late on most days, and a tighter
  deadline would alert on a healthy pipeline.
- **Heartbeat.** The newest load is younger than 36 hours, the same threshold
  as the dbt source freshness error and the app's "Last loaded" tone.
"""

from datetime import UTC, date, datetime, timedelta
import sys

import duckdb

from power_market_lens.config import load_settings
from power_market_lens.ingest.warehouse import connect
from power_market_lens.zones import SDAC_TIMEZONE, ZONES

MAX_LOAD_AGE = timedelta(hours=36)

LatestByZone = dict[str, tuple[date, datetime]]


def required_delivery_date(now: datetime) -> date:
    """The delivery day every zone must have prices for at `now`: today, market time."""
    return now.astimezone(SDAC_TIMEZONE).date()


def problems(latest: LatestByZone, zone_codes: list[str], now: datetime) -> list[str]:
    """Why the marts are stale at `now`, one line per finding; empty when fresh.

    `latest` maps a zone code to its newest delivery date and newest load time.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    required = required_delivery_date(now)
    found: list[str] = []
    for code in zone_codes:
        if code not in latest:
            found.append(f"{code}: no prices in the marts at all")
            continue
        last_day, _ = latest[code]
        if last_day < required:
            found.append(f"{code}: prices end on {last_day}, expected through {required}")
    if latest:
        newest_load = max(loaded_at for _, loaded_at in latest.values())
        age = now - newest_load
        if age > MAX_LOAD_AGE:
            found.append(
                f"newest load is {age.total_seconds() / 3600:.0f} hours old "
                f"({newest_load:%Y-%m-%d %H:%M} UTC), limit {MAX_LOAD_AGE.total_seconds() / 3600:.0f}"
            )
    return found


def latest_by_zone(con: duckdb.DuckDBPyConnection) -> LatestByZone:
    try:
        rows = con.execute(
            "select zone_code, max(delivery_date_local), max(loaded_at) "
            "from marts.fct_day_ahead_price_hourly group by zone_code"
        ).fetchall()
    except duckdb.CatalogException:
        return {}  # no marts yet: every zone is reported as missing
    return {code: (last_day, loaded_at.astimezone(UTC)) for code, last_day, loaded_at in rows}


def main() -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with connect(settings) as con:
        latest = latest_by_zone(con)
    found = problems(latest, sorted(ZONES), now)
    if not found:
        print(
            f"{settings.database} is fresh: every zone has prices through "
            f"{required_delivery_date(now)}"
        )
        return
    print(f"{settings.database} is stale at {now:%Y-%m-%d %H:%M} UTC:")
    for line in found:
        print(f"  - {line}")
    sys.exit(1)


if __name__ == "__main__":
    main()
