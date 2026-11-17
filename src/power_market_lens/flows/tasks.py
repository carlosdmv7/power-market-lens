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

import duckdb
from prefect import get_run_logger, task
from prefect.runtime import flow_run

from power_market_lens.config import Settings, load_settings
from power_market_lens.flows.windows import RunWindows, Window
from power_market_lens.ingest import eurostat
from power_market_lens.ingest.entsoe_client import EntsoeAuthError, EntsoeClient
from power_market_lens.ingest.jobs import (
    load_crossborder_flows,
    load_day_ahead_prices,
    load_generation_per_type,
    load_total_load_actual,
    load_total_load_forecast,
)
from power_market_lens.ingest.warehouse import connect, ensure_raw_tables
from power_market_lens.monitor import job as monitor_job
from power_market_lens.zones import SDAC_TIMEZONE, get_zone

DBT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "transform"

JOBS: dict[str, Callable[..., int]] = {
    "day_ahead_prices": load_day_ahead_prices,
    "generation_per_type": load_generation_per_type,
    "total_load_actual": load_total_load_actual,
    "total_load_forecast": load_total_load_forecast,
    "crossborder_flows": load_crossborder_flows,
}


# Which window each entity is fetched over. What is published a day ahead
# (prices, the load forecast) reaches into tomorrow; what is measured (actual
# generation, load and physical flows) stops at the current hour.
WINDOW_OF = {
    "day_ahead_prices": "day_ahead",
    "generation_per_type": "measured",
    "total_load_actual": "measured",
    "total_load_forecast": "day_ahead",
    "crossborder_flows": "measured",
}


def make_client(settings: Settings) -> EntsoeClient:
    return EntsoeClient.from_settings(settings)


def make_connection(settings: Settings) -> duckdb.DuckDBPyConnection:
    return connect(settings)


def make_eurostat_fetch() -> eurostat.Fetch:
    return eurostat.fetch_dataset


def current_run_id() -> str:
    return str(flow_run.id) if flow_run.id else "manual"


def _worth_retrying(task, task_run, state) -> bool:  # noqa: ARG001 - Prefect's signature
    """Prefect asks this before each task retry. A rejected token stays rejected."""
    return not isinstance(state.result(raise_on_failure=False), EntsoeAuthError)


@task(
    retries=2,
    retry_delay_seconds=60,
    retry_condition_fn=_worth_retrying,
    task_run_name="load {entity} {zone_code}",
)
def load_entity(entity: str, zone_code: str, window: Window) -> int:
    """Fetch one entity for one zone and window from ENTSO-E; append it to raw."""
    settings = load_settings()
    client = make_client(settings)
    with make_connection(settings) as con:
        ensure_raw_tables(con)
        return JOBS[entity](
            client, con, get_zone(zone_code), window.start, window.end, run_id=current_run_id()
        )


# Consecutive failed loads after which the rest of the run is skipped. One
# failed load is usually one zone the publisher is late on; several in a row,
# across entities, mean ENTSO-E itself is down. Each failed load costs minutes
# of backoff and task retries, so without a stop a full outage spends the whole
# job timeout on loads that cannot succeed.
MAX_CONSECUTIVE_FAILURES = 4


def load_zones(zone_codes: list[str], windows: RunWindows) -> tuple[dict[str, int], list[str]]:
    """Run every (zone, entity) load, whatever happens to the others.

    A load that still fails after its retries is recorded, not raised: one zone
    the publisher is late on must not leave every other zone stale. The caller
    builds dbt on what did arrive, then fails the run with `raise_for_failed`.
    Returns the row count per "zone/entity" that loaded, and those that failed.

    The one exception is a rejected token (`EntsoeAuthError`): every remaining
    load would be refused as well, so it is raised at once and the run ends
    red within seconds. From 2026-11-06 to 2026-11-14 an expired token was
    instead retried load by load until the job timeout cancelled each run,
    which GitHub does not report as a failure.

    After MAX_CONSECUTIVE_FAILURES failed loads in a row the remaining loads
    are not attempted; they are returned as failed, marked "(skipped)".
    """
    counts: dict[str, int] = {}
    failed: list[str] = []
    keys = [(zone_code, entity) for zone_code in zone_codes for entity in JOBS]
    consecutive = 0
    for position, (zone_code, entity) in enumerate(keys):
        window = getattr(windows, WINDOW_OF[entity])
        state = load_entity(entity, zone_code, window, return_state=True)
        key = f"{zone_code}/{entity}"
        if state.is_completed():
            counts[key] = state.result()
            consecutive = 0
            continue
        error = state.result(raise_on_failure=False)
        if isinstance(error, EntsoeAuthError):
            raise error
        failed.append(key)
        consecutive += 1
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            skipped = [f"{z}/{e} (skipped)" for z, e in keys[position + 1 :]]
            get_run_logger().error(
                "%d loads failed in a row: ENTSO-E looks unavailable. Skipping the "
                "remaining %d loads; dbt is built on what arrived.",
                consecutive,
                len(skipped),
            )
            failed.extend(skipped)
            break
    return counts, failed


@task(retries=2, retry_delay_seconds=60, task_run_name="load eurostat {code}")
def load_eurostat_dataset(code: str) -> int:
    """Append one Eurostat dataset to raw when Eurostat has updated it (ADR-010)."""
    settings = load_settings()
    with make_connection(settings) as con:
        ensure_raw_tables(con)
        return eurostat.load_dataset(
            con, code, fetch=make_eurostat_fetch(), run_id=current_run_id()
        )


def load_household_prices() -> tuple[dict[str, int], list[str]]:
    """Every Eurostat dataset, with the same rule as the ENTSO-E loads: a
    failure is recorded, not raised, so it never holds back the market data."""
    counts: dict[str, int] = {}
    failed: list[str] = []
    for code in eurostat.DATASETS:
        state = load_eurostat_dataset(code, return_state=True)
        key = f"eurostat/{code}"
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


MONITOR_KEY = "monitor/system_conditions"


@task(task_run_name="assess system conditions")
def assess_system_conditions() -> int:
    """Score every zone's hours for unusual conditions and replace the monitor
    table (ADR-008). Reads only the marts, so it runs after `dbt build`."""
    with make_connection(load_settings()) as con:
        return monitor_job.run(con)


def run_monitor(target: str) -> list[str]:
    """The monitor, then dbt's tests on the table it wrote.

    The tests run here, not in `dbt build`: the build comes first and would
    test the previous run's table (selectors.yml keeps them out of it). With
    the loads' rule, a failure of either is recorded, not raised, so it never
    holds back the freshness check; the run fails at the end.
    """
    state = assess_system_conditions(return_state=True)
    if not state.is_completed():
        return [MONITOR_KEY]
    if state.result() == 0:
        return []  # nothing written (no marts, or too little history): nothing to test
    tested = test_monitor_table(target, return_state=True)
    return [] if tested.is_completed() else [MONITOR_KEY]


def raise_for_failed(failed: list[str]) -> None:
    if not failed:
        return
    skipped = [key for key in failed if key.endswith("(skipped)")]
    attempted = [key for key in failed if key not in skipped and key != MONITOR_KEY]
    parts = []
    if attempted:
        parts.append(
            f"{len(attempted)} load(s) failed after their retries: {', '.join(attempted)}."
        )
    if skipped:
        parts.append(
            f"After {MAX_CONSECUTIVE_FAILURES} failures in a row ENTSO-E looked unavailable, "
            f"so {len(skipped)} load(s) were not attempted."
        )
    if attempted or skipped:
        parts.append("dbt was built on the loads that succeeded.")
    if MONITOR_KEY in failed:
        parts.append(
            "The monitor of unusual system conditions failed, or its table failed its "
            "dbt tests (see the task log); the marts were built."
        )
    raise RuntimeError(" ".join(parts))


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


@task(task_run_name="test the monitor table ({target})")
def test_monitor_table(target: str) -> None:
    """dbt's tests on the table the monitor has just written (ADR-008)."""
    _export_motherduck_token()
    if not run_dbt(["test", "--select", "source:monitor", "--target", target]):
        raise RuntimeError(f"The monitor table failed its dbt tests on target {target}")
