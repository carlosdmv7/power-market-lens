"""Daily refresh: re-pull the trailing window for every zone, dbt build, then
check that ENTSO-E is still delivering (source freshness).

Run as a plain process (ADR-002): `python -m power_market_lens.flows.daily`.
With PREFECT_API_URL and PREFECT_API_KEY set, the run is recorded in Prefect
Cloud; without them it runs against a temporary local Prefect server.
APP_ENV picks the database and the dbt target (dev or prod).
"""

from datetime import UTC, datetime

from prefect import flow, get_run_logger

from power_market_lens.config import load_settings
from power_market_lens.flows.tasks import JOBS, dbt_build, load_entity, source_freshness
from power_market_lens.flows.windows import daily_windows
from power_market_lens.zones import ZONES


@flow(name="power-market-lens-daily")
def daily_refresh(
    zones: list[str] | None = None, trailing_days: int | None = None
) -> dict[str, int]:
    settings = load_settings()
    logger = get_run_logger()
    windows = daily_windows(datetime.now(UTC), trailing_days or settings.trailing_window_days)
    logger.info(
        "Daily refresh of %s into %s: %s", zones or sorted(ZONES), settings.database, windows
    )

    counts = {}
    for zone_code in zones or sorted(ZONES):
        for entity in JOBS:
            window = windows.prices if entity == "day_ahead_prices" else windows.generation
            counts[f"{zone_code}/{entity}"] = load_entity(entity, zone_code, window)

    dbt_build(settings.app_env)
    # After the build, so the marts are refreshed with whatever did arrive
    # before a stale source fails the run.
    source_freshness(settings.app_env)
    return counts


if __name__ == "__main__":
    daily_refresh()
