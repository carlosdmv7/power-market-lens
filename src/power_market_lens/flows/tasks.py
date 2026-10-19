"""Prefect tasks shared by the daily and backfill flows.

Each task builds its own client and connection from the environment, so a
retry starts clean. The factories are module-level functions so the tests can
swap them for fakes.
"""

from collections.abc import Callable
import os
from pathlib import Path
import subprocess  # noqa: S404 - runs this environment's own dbt CLI
import sys

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
