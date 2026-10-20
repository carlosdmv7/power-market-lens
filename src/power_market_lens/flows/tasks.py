"""Prefect tasks shared by the daily and backfill flows.

Each task builds its own client and connection from the environment, so a
retry starts clean. The factories are module-level functions so the tests can
swap them for fakes.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
import os
from pathlib import Path
import subprocess  # noqa: S404 - runs this environment's own dbt CLI
import sys
from zoneinfo import ZoneInfo

import duckdb
from prefect import task
from prefect.runtime import flow_run

from power_market_lens.config import Settings, load_settings
from power_market_lens.flows.windows import RunWindows, Window
from power_market_lens.ingest.entsoe_client import EntsoeClient
from power_market_lens.ingest.jobs import (
    load_day_ahead_prices,
    load_generation_per_type,
    load_total_load_actual,
    load_total_load_forecast,
)
from power_market_lens.ingest.warehouse import connect, ensure_raw_tables
from power_market_lens.zones import get_zone

SDAC_TIMEZONE = ZoneInfo("Europe/Brussels")  # = dbt var sdac_market_timezone
DBT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "transform"

JOBS: dict[str, Callable[..., int]] = {
    "day_ahead_prices": load_day_ahead_prices,
    "generation_per_type": load_generation_per_type,
    "total_load_actual": load_total_load_actual,
    "total_load_forecast": load_total_load_forecast,
}


# Which window each entity is fetched over. What is published a day ahead
# (prices, the load forecast) reaches into tomorrow; what is measured (actual
# generation and load) stops at the current hour.
WINDOW_OF = {
    "day_ahead_prices": "day_ahead",
    "generation_per_type": "measured",
    "total_load_actual": "measured",
    "total_load_forecast": "day_ahead",
}


def make_client(settings: Settings) -> EntsoeClient:
    return EntsoeClient.from_settings(settings)


def make_connection(settings: Settings) -> duckdb.DuckDBPyConnection:
    return connect(settings)


def current_run_id() -> str:
    return str(flow_run.id) if flow_run.id else "manual"


@task(retries=2, retry_delay_seconds=60, task_run_name="load {entity} {zone_code}")
def load_entity(entity: str, zone_code: str, window: Window) -> int:
    """Fetch one entity for one zone and window from ENTSO-E; append it to raw."""
    settings = load_settings()
    client = make_client(settings)
    with make_connection(settings) as con:
        ensure_raw_tables(con)
        return JOBS[entity](
            client, con, get_zone(zone_code), window.start, window.end, run_id=current_run_id()
        )


def load_zones(zone_codes: list[str], windows: RunWindows) -> tuple[dict[str, int], list[str]]:
    """Run every (zone, entity) load, whatever happens to the others.

    A load that still fails after its retries is recorded, not raised: one zone
    the publisher is late on must not leave every other zone stale. The caller
    builds dbt on what did arrive, then fails the run with `raise_for_failed`.
    Returns the row count per "zone/entity" that loaded, and those that failed.
    """
    counts: dict[str, int] = {}
    failed: list[str] = []
    for zone_code in zone_codes:
        for entity in JOBS:
            window = getattr(windows, WINDOW_OF[entity])
            state = load_entity(entity, zone_code, window, return_state=True)
            key = f"{zone_code}/{entity}"
            if state.is_completed():
                counts[key] = state.result()
            else:
                failed.append(key)
    return counts, failed


def tomorrows_prices_built(zone_codes: list[str], now: datetime) -> bool:
    """True when the marts already hold tomorrow's day-ahead prices for every zone.

    That is the day's work done: the prices are published once a day, and a
    run that built them also re-pulled the trailing window. GitHub drops many
    scheduled runs, so the pipeline is scheduled several times a day and the
    later attempts use this to skip. Tomorrow is the market day in Brussels
    time, the clock SDAC runs on; every zone's local calendar reaches it once
    that day's auction is loaded.
    """
    tomorrow = (now.astimezone(SDAC_TIMEZONE) + timedelta(days=1)).date()
    with make_connection(load_settings()) as con:
        try:
            latest = dict(
                con.execute(
                    "select zone_code, max(delivery_date_local) "
                    "from marts.fct_day_ahead_price_hourly group by zone_code"
                ).fetchall()
            )
        except duckdb.CatalogException:
            return False  # no marts yet: nothing has been built
    return all(latest.get(code) is not None and latest[code] >= tomorrow for code in zone_codes)


def raise_for_failed(failed: list[str]) -> None:
    if failed:
        raise RuntimeError(
            f"{len(failed)} load(s) failed after their retries: {', '.join(failed)}. "
            "Every other load ran and dbt was built on it."
        )


def run_dbt(args: list[str]) -> bool:
    """Run the dbt CLI of this environment in its own process.

    Not in-process (dbtRunner): DuckDB caches one database instance per path
    and process, so after a load task had opened md:power_<env>, dbt-duckdb's
    connection with a different configuration was refused ("Can't open a
    connection to same database file with a different configuration").
    """
    dbt = Path(sys.executable).with_name("dbt")
    project = str(DBT_PROJECT_DIR)
    command = [str(dbt), *args, "--project-dir", project, "--profiles-dir", project]
    return subprocess.run(command, check=False).returncode == 0


def _export_motherduck_token() -> None:
    # dbt reads the token from the environment for its `md:` path; locally it
    # may only be in .env, which Settings reads but does not export.
    settings = load_settings()
    if settings.motherduck_token is not None:
        os.environ.setdefault("MOTHERDUCK_TOKEN", settings.motherduck_token.get_secret_value())


@task(task_run_name="dbt build ({target})")
def dbt_build(target: str) -> None:
    """Install packages, then seeds, models and tests against `target`."""
    _export_motherduck_token()
    if not run_dbt(["deps"]):
        raise RuntimeError("dbt deps failed")
    if not run_dbt(["build", "--target", target]):
        raise RuntimeError(f"dbt build failed on target {target}")


@task(task_run_name="source freshness ({target})")
def source_freshness(target: str) -> None:
    """Fail the run when ENTSO-E has stopped delivering.

    Judged on the newest delivery interval in raw (the sources' loaded_at_field),
    so a run that succeeds but fetches nothing new still goes red once the data
    is older than `error_after`. dbt exits 0 on `warn_after`: a warning is
    logged, the run stays green.
    """
    _export_motherduck_token()
    if not run_dbt(["source", "freshness", "--target", target]):
        raise RuntimeError(
            f"Source freshness failed on target {target}: the newest ENTSO-E data is "
            "older than error_after (see the dbt output above)"
        )
