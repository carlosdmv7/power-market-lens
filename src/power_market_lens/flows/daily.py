"""Daily refresh: re-pull the trailing window for every zone, dbt build, then
check that ENTSO-E is still delivering (source freshness). A zone whose load
fails does not stop the others; the run fails at the end, naming it.

Run as a plain process (ADR-002): `python -m power_market_lens.flows.daily`.
Scheduled runs add `--skip-if-done`: the pipeline is scheduled several times a
day because GitHub drops scheduled runs, and only the first attempt that finds
tomorrow's prices missing does the work.
With PREFECT_API_URL and PREFECT_API_KEY set, the run is recorded in Prefect
Cloud; without them it runs against a temporary local Prefect server.
APP_ENV picks the database and the dbt target (dev or prod).
"""

import argparse
from datetime import UTC, datetime

from prefect import flow, get_run_logger

from power_market_lens.config import load_settings
from power_market_lens.flows.tasks import (
    dbt_build,
    load_zones,
    raise_for_failed,
    source_freshness,
    tomorrows_prices_built,
)
from power_market_lens.flows.windows import daily_windows
from power_market_lens.zones import ZONES


@flow(name="power-market-lens-daily")
def daily_refresh(
    zones: list[str] | None = None,
    trailing_days: int | None = None,
    skip_if_done: bool = False,
) -> dict[str, int]:
    settings = load_settings()
    logger = get_run_logger()
    now = datetime.now(UTC)
    zone_codes = zones or sorted(ZONES)
    if skip_if_done and _done(zone_codes, now, logger):
        logger.info("Tomorrow's prices are already built for every zone; nothing to do")
        return {}
    windows = daily_windows(now, trailing_days or settings.trailing_window_days)
    logger.info("Daily refresh of %s into %s: %s", zone_codes, settings.database, windows)

    counts, failed = load_zones(zone_codes, windows)
    dbt_build(settings.app_env)
    # After the build, so the marts are refreshed with whatever did arrive
    # before a stale source fails the run.
    source_freshness(settings.app_env)
    raise_for_failed(failed)
    return counts


def _done(zone_codes: list[str], now: datetime, logger) -> bool:
    try:
        return tomorrows_prices_built(zone_codes, now)
    except Exception:  # noqa: BLE001 - a failed check must never skip the refresh
        logger.warning("Could not check whether today's work is done; refreshing", exc_info=True)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-if-done",
        action="store_true",
        help="exit without loading when tomorrow's prices are already built (scheduled runs)",
    )
    daily_refresh(skip_if_done=parser.parse_args().skip_if_done)


if __name__ == "__main__":
    main()
