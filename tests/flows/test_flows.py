"""The flows end to end against a temporary local Prefect server, with ENTSO-E
replaced by recorded fixtures, MotherDuck by a DuckDB file and dbt by a stub."""

from datetime import date
from pathlib import Path

import duckdb
from prefect.testing.utilities import prefect_test_harness
import pytest
import requests

from power_market_lens.flows import daily, tasks
from power_market_lens.flows.backfill import backfill
from power_market_lens.flows.daily import daily_refresh
from power_market_lens.ingest.entsoe_client import EntsoeClient

FIXTURES = Path(__file__).parents[1] / "ingest" / "fixtures" / "entsoe"


class _ReplayRawClient:
    def query_day_ahead_prices(self, *_, **__):
        return (FIXTURES / "synthetic_prices_a03_15min_dst_autumn.xml").read_text()

    def query_generation(self, *_, **__):
        return (FIXTURES / "synthetic_generation_per_type.xml").read_text()


@pytest.fixture(scope="module", autouse=True)
def prefect_server():
    with prefect_test_harness():
        yield


@pytest.fixture
def wired(monkeypatch, tmp_path):
    # Not "raw.duckdb": DuckDB names the catalog after the file, and a catalog
    # called raw makes the schema raw ambiguous.
    database = tmp_path / "warehouse.duckdb"
    dbt_calls = []
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(
        tasks,
        "make_client",
        lambda _: EntsoeClient(_ReplayRawClient(), chunk_days=400, sleep=lambda _: None),
    )
    monkeypatch.setattr(tasks, "make_connection", lambda _: duckdb.connect(database))
    monkeypatch.setattr(tasks, "run_dbt", lambda args: dbt_calls.append(args) or True)
    return database, dbt_calls


def test_daily_refresh_loads_both_entities_then_builds_dbt(wired):
    database, dbt_calls = wired

    counts = daily_refresh(zones=["ES"], trailing_days=7)

    assert counts == {"ES/day_ahead_prices": 100, "ES/generation_per_type": 7}
    assert dbt_calls == [
        ["deps"],
        ["build", "--target", "dev"],
        ["source", "freshness", "--target", "dev"],
    ]
    run_ids = (
        duckdb.connect(database)
        .execute("select distinct _run_id from raw.entsoe_day_ahead_prices")
        .fetchall()
    )
    assert len(run_ids) == 1
    assert run_ids[0][0] != "manual"  # the Prefect flow-run id


def test_backfill_twice_appends_twice(wired):
    database, _ = wired

    backfill(date(2025, 10, 25), date(2025, 10, 26), zones=["ES"])
    backfill(date(2025, 10, 25), date(2025, 10, 26), zones=["ES"])

    total, distinct_points = (
        duckdb.connect(database)
        .execute(
            "select count(*), count(distinct period_start_utc) from raw.entsoe_day_ahead_prices"
        )
        .fetchone()
    )
    assert (total, distinct_points) == (200, 100)


def test_a_failed_load_does_not_stop_the_others(wired, monkeypatch):
    database, dbt_calls = wired

    class _GenerationRefused(_ReplayRawClient):
        def query_generation(self, *_, **__):
            response = requests.Response()
            response.status_code = 401  # not retryable: fails at once
            raise requests.HTTPError(response=response)

    monkeypatch.setattr(
        tasks,
        "make_client",
        lambda _: EntsoeClient(_GenerationRefused(), chunk_days=400, sleep=lambda _: None),
    )
    # No 60-second Prefect retries in a test.
    monkeypatch.setattr(tasks, "load_entity", tasks.load_entity.with_options(retries=0))

    with pytest.raises(RuntimeError, match=r"1 load\(s\) failed.*ES/generation_per_type"):
        daily_refresh(zones=["ES"], trailing_days=1)

    prices = (
        duckdb.connect(database)
        .execute("select count(*) from raw.entsoe_day_ahead_prices")
        .fetchone()[0]
    )
    assert prices == 100  # the other entity still loaded
    assert ["build", "--target", "dev"] in dbt_calls  # and dbt was built on it


def test_a_failed_dbt_build_fails_the_flow(wired, monkeypatch):
    monkeypatch.setattr(tasks, "run_dbt", lambda args: args == ["deps"])

    with pytest.raises(RuntimeError, match="dbt build failed"):
        daily_refresh(zones=["ES"], trailing_days=1)


def test_stale_sources_fail_the_daily_run_after_the_build(wired, monkeypatch):
    _, dbt_calls = wired
    monkeypatch.setattr(
        tasks, "run_dbt", lambda args: dbt_calls.append(args) or args[0] != "source"
    )

    with pytest.raises(RuntimeError, match="Source freshness failed"):
        daily_refresh(zones=["ES"], trailing_days=1)
    assert ["build", "--target", "dev"] in dbt_calls  # the marts were still refreshed


def test_run_dbt_reports_the_cli_exit_status():
    # The real CLI, in its own process; neither command touches a database.
    assert tasks.run_dbt(["debug", "--config-dir"]) is True
    assert tasks.run_dbt(["no-such-command"]) is False


def _marts_with_prices_through(database, delivery_date: str) -> None:
    con = duckdb.connect(database)
    con.execute("create schema marts")
    con.execute(
        "create table marts.fct_day_ahead_price_hourly as "
        f"select 'ES' as zone_code, date '{delivery_date}' as delivery_date_local"
    )
    con.close()


def test_a_scheduled_run_skips_once_tomorrows_prices_are_built(wired):
    database, dbt_calls = wired
    _marts_with_prices_through(database, "2999-01-01")  # always beyond tomorrow

    assert daily_refresh(zones=["ES"], skip_if_done=True) == {}
    assert dbt_calls == []  # no load, no build


def test_a_scheduled_run_refreshes_while_tomorrow_is_missing(wired):
    database, dbt_calls = wired
    _marts_with_prices_through(database, "2000-01-01")

    counts = daily_refresh(zones=["ES"], trailing_days=1, skip_if_done=True)

    assert counts["ES/day_ahead_prices"] == 100
    assert ["build", "--target", "dev"] in dbt_calls


def test_a_failed_check_never_skips_the_refresh(wired, monkeypatch):
    _, dbt_calls = wired

    def broken(*_):
        raise RuntimeError("MotherDuck unavailable")

    monkeypatch.setattr(daily, "tomorrows_prices_built", broken)

    counts = daily_refresh(zones=["ES"], trailing_days=1, skip_if_done=True)

    assert counts["ES/day_ahead_prices"] == 100
    assert ["build", "--target", "dev"] in dbt_calls
