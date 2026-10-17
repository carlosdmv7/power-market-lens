"""Prefect tasks shared by the daily and backfill flows.

Each task builds its own client and connection from the environment, so a
retry starts clean. The factories are module-level functions so the tests can
swap them for fakes.
"""

from collections.abc import Callable
import os
from pathlib import Path

import duckdb
from prefect import task
from prefect.runtime import flow_run

from power_market_lens.config import Settings, load_settings
from power_market_lens.flows.windows import Window
from power_market_lens.ingest.entsoe_client import EntsoeClient
from power_market_lens.ingest.jobs import load_day_ahead_prices, load_generation_per_type
from power_market_lens.ingest.warehouse import connect, ensure_raw_tables
from power_market_lens.zones import get_zone

DBT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "transform"

JOBS: dict[str, Callable[..., int]] = {
    "day_ahead_prices": load_day_ahead_prices,
    "generation_per_type": load_generation_per_type,
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


def run_dbt(args: list[str]) -> bool:
    from dbt.cli.main import dbtRunner  # noqa: PLC0415 - heavy import, only when dbt runs

    result = dbtRunner().invoke(
        [*args, "--project-dir", str(DBT_PROJECT_DIR), "--profiles-dir", str(DBT_PROJECT_DIR)]
    )
    return result.success


@task(task_run_name="dbt build ({target})")
def dbt_build(target: str) -> None:
    """Install packages, then seeds, models and tests against `target`."""
    settings = load_settings()
    # dbt reads the token from the environment for its `md:` path; locally it
    # may only be in .env, which Settings reads but does not export.
    if settings.motherduck_token is not None:
        os.environ.setdefault("MOTHERDUCK_TOKEN", settings.motherduck_token.get_secret_value())
    if not run_dbt(["deps"]):
        raise RuntimeError("dbt deps failed")
    if not run_dbt(["build", "--target", target]):
        raise RuntimeError(f"dbt build failed on target {target}")
