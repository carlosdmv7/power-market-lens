"""Backfill: load a date range for every zone, then dbt build.

    python -m power_market_lens.flows.backfill --start 2026-07-19 --end 2026-10-17

The client splits the range into 30-day requests with backoff, so a multi-year
backfill respects ENTSO-E's rate limits. Raw is append-only and staging keeps
the latest load, so running the same backfill twice changes nothing downstream.
"""

import argparse
from datetime import UTC, date, datetime

from prefect import flow, get_run_logger

from power_market_lens.config import load_settings
from power_market_lens.flows.tasks import JOBS, dbt_build, load_entity
from power_market_lens.flows.windows import backfill_windows
from power_market_lens.zones import ZONES


@flow(name="power-market-lens-backfill")
def backfill(start_date: date, end_date: date, zones: list[str] | None = None) -> dict[str, int]:
    settings = load_settings()
    logger = get_run_logger()
    windows = backfill_windows(start_date, end_date, datetime.now(UTC))
    logger.info("Backfill %s–%s into %s", start_date, end_date, settings.database)

    counts = {}
    for zone_code in zones or sorted(ZONES):
        for entity in JOBS:
            window = windows.prices if entity == "day_ahead_prices" else windows.generation
            counts[f"{zone_code}/{entity}"] = load_entity(entity, zone_code, window)

    dbt_build(settings.app_env)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--zones", nargs="*", default=None, help="zone codes, default all")
    args = parser.parse_args()
    backfill(args.start, args.end, args.zones)


if __name__ == "__main__":
    main()
