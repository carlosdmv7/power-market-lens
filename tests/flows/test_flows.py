"""The flows end to end against a temporary local Prefect server, with ENTSO-E
replaced by recorded fixtures, MotherDuck by a DuckDB file and dbt by a stub."""

from datetime import date
import json
from pathlib import Path

import duckdb
from prefect.testing.utilities import prefect_test_harness
import pytest
import requests

from power_market_lens.flows import daily, tasks
from power_market_lens.flows.backfill import backfill
from power_market_lens.flows.daily import daily_refresh
from power_market_lens.ingest.entsoe_client import EntsoeAuthError, EntsoeClient

FIXTURES = Path(__file__).parents[1] / "ingest" / "fixtures" / "entsoe"
EUROSTAT = Path(__file__).parents[1] / "ingest" / "fixtures" / "eurostat"


class _ReplayRawClient:
    def query_day_ahead_prices(self, *_, **__):
        return (FIXTURES / "synthetic_prices_a03_15min_dst_autumn.xml").read_text()

    def query_generation(self, *_, **__):
        return (FIXTURES / "synthetic_generation_per_type.xml").read_text()

    def query_load(self, *_, **__):
        return (FIXTURES / "recorded_load_a65_actual_es_2026-03-29.xml").read_text()

    def query_load_forecast(self, *_, **__):
        return (FIXTURES / "recorded_load_a65_forecast_es_2026-03-29.xml").read_text()

    def query_crossborder_flows(self, *_, **__):
        return (FIXTURES / "recorded_flows_a11_es-fr_2026-03-29.xml").read_text()


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
    monkeypatch.setattr(
        tasks,
        "make_eurostat_fetch",
        lambda: (
            lambda dataset: json.loads((EUROSTAT / f"recorded_{dataset.code}.json").read_text())
        ),
    )
    return database, dbt_calls


def test_daily_refresh_loads_both_entities_then_builds_dbt(wired):
    database, dbt_calls = wired

    counts = daily_refresh(zones=["ES"], trailing_days=7)

    assert counts == {
        "ES/day_ahead_prices": 100,
        "ES/generation_per_type": 7,
        "ES/total_load_actual": 92,
        "ES/total_load_forecast": 92,
        "ES/crossborder_flows": 4 * 92,  # ES-PT and ES-FR, both directions
        "eurostat/nrg_pc_204": 3706,
        "eurostat/nrg_pc_204_c": 3481,
    }
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


def test_repeating_a_backfill_stores_nothing_twice(wired):
    # Backfills are safe to repeat (runbook): the second finds every point
    # unchanged and appends nothing (ADR-011).
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
    assert (total, distinct_points) == (100, 100)


def test_a_failed_load_does_not_stop_the_others(wired, monkeypatch):
    database, dbt_calls = wired

    class _GenerationRefused(_ReplayRawClient):
        def query_generation(self, *_, **__):
            response = requests.Response()
            response.status_code = 400  # not retryable: fails at once
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


def test_a_rejected_token_stops_the_run_at_the_first_load(wired, monkeypatch):
    _, dbt_calls = wired
    requests_made = []

    class _TokenRejected(_ReplayRawClient):
        def query_day_ahead_prices(self, *_, **__):
            requests_made.append("prices")
            response = requests.Response()
            response.status_code = 401
            raise requests.HTTPError(response=response)

    monkeypatch.setattr(
        tasks,
        "make_client",
        lambda _: EntsoeClient(_TokenRejected(), chunk_days=400, sleep=lambda _: None),
    )

    # The task keeps its two 60-second retries: were the error retried, this
    # test would take minutes instead of failing fast.
    with pytest.raises(EntsoeAuthError, match="rejected the security token"):
        daily_refresh(zones=["ES", "PT"], trailing_days=1)

    assert requests_made == ["prices"]  # no retry, no second entity, no second zone
    assert dbt_calls == []  # nothing new arrived, so nothing is rebuilt


def test_an_entsoe_outage_stops_after_consecutive_failures(wired, monkeypatch):
    _, dbt_calls = wired
    queried = []

    class _Down(_ReplayRawClient):
        def __getattribute__(self, name):
            if name.startswith("query_"):

                def refuse(*_, **__):
                    queried.append(name)
                    response = requests.Response()
                    response.status_code = 503
                    raise requests.HTTPError(response=response)

                return refuse
            return super().__getattribute__(name)

    monkeypatch.setattr(
        tasks,
        "make_client",
        lambda _: EntsoeClient(_Down(), chunk_days=400, sleep=lambda _: None),
    )
    monkeypatch.setattr(tasks, "load_entity", tasks.load_entity.with_options(retries=0))

    with pytest.raises(RuntimeError, match=r"4 load\(s\) failed.*6 load\(s\) were not attempted"):
        daily_refresh(zones=["ES", "PT"], trailing_days=1)

    # Four loads, each tried four times by the client's backoff; PT never asked.
    assert len(queried) == 4 * 4
    assert "query_crossborder_flows" not in queried
    assert ["build", "--target", "dev"] in dbt_calls  # dbt still built on what arrived


def test_one_failed_load_resets_after_a_success(wired, monkeypatch):
    class _GenerationDown(_ReplayRawClient):
        def query_generation(self, *_, **__):
            response = requests.Response()
            response.status_code = 503
            raise requests.HTTPError(response=response)

    monkeypatch.setattr(
        tasks,
        "make_client",
        lambda _: EntsoeClient(_GenerationDown(), chunk_days=400, sleep=lambda _: None),
    )
    monkeypatch.setattr(tasks, "load_entity", tasks.load_entity.with_options(retries=0))

    # Generation fails in all three zones, but never four loads in a row.
    with pytest.raises(RuntimeError) as raised:
        daily_refresh(zones=["ES", "PT", "FR"], trailing_days=1)

    assert str(raised.value).startswith("3 load(s) failed")
    assert "not attempted" not in str(raised.value)


def test_a_failed_dbt_build_fails_the_flow(wired, monkeypatch):
    monkeypatch.setattr(tasks, "run_dbt", lambda args: args == ["deps"])

    with pytest.raises(RuntimeError, match="dbt build failed"):
        daily_refresh(zones=["ES"], trailing_days=1)


def test_the_monitor_runs_after_the_build_on_the_marts(wired, monkeypatch):
    database, dbt_calls = wired
    seen = []

    def run(con):
        seen.append(list(dbt_calls))  # what dbt had done when the monitor started
        return 0

    monkeypatch.setattr(tasks.monitor_job, "run", run)

    daily_refresh(zones=["ES"], trailing_days=1)

    assert seen == [[["deps"], ["build", "--target", "dev"]]]
    # It wrote nothing, so there is no table for dbt to test.
    assert ["test", "--select", "source:monitor", "--target", "dev"] not in dbt_calls


def test_dbt_tests_the_monitor_table_right_after_it_is_written(wired, monkeypatch):
    _, dbt_calls = wired
    monkeypatch.setattr(tasks.monitor_job, "run", lambda con: 1000)

    daily_refresh(zones=["ES"], trailing_days=1)

    assert dbt_calls == [
        ["deps"],
        ["build", "--target", "dev"],
        ["test", "--select", "source:monitor", "--target", "dev"],
        ["source", "freshness", "--target", "dev"],
    ]


def test_a_monitor_table_failing_its_tests_fails_the_run(wired, monkeypatch):
    _, dbt_calls = wired
    monkeypatch.setattr(tasks.monitor_job, "run", lambda con: 1000)
    monkeypatch.setattr(tasks, "run_dbt", lambda args: dbt_calls.append(args) or args[0] != "test")

    with pytest.raises(RuntimeError, match="failed its dbt tests"):
        daily_refresh(zones=["ES"], trailing_days=1)
    assert ["source", "freshness", "--target", "dev"] in dbt_calls  # still checked


def test_a_failed_monitor_fails_the_run_after_the_freshness_check(wired, monkeypatch):
    _, dbt_calls = wired

    def broken(con):
        raise ValueError("the SVD did not converge")

    monkeypatch.setattr(tasks.monitor_job, "run", broken)

    with pytest.raises(RuntimeError, match="monitor of unusual system conditions failed"):
        daily_refresh(zones=["ES"], trailing_days=1)
    assert ["source", "freshness", "--target", "dev"] in dbt_calls  # still checked


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


def test_eurostat_is_appended_once_per_published_version(wired):
    database, _ = wired

    daily_refresh(zones=["ES"], trailing_days=1)
    counts = daily_refresh(zones=["ES"], trailing_days=1)

    assert counts["eurostat/nrg_pc_204"] == 0  # same version: nothing appended
    rows = (
        duckdb.connect(database)
        .execute("select count(*) from raw.eurostat_household_prices")
        .fetchone()[0]
    )
    assert rows == 3706


def test_a_failed_eurostat_load_does_not_stop_the_market_data(wired, monkeypatch):
    database, dbt_calls = wired

    def down(_dataset):
        raise requests.ConnectionError("Eurostat unreachable")

    monkeypatch.setattr(tasks, "make_eurostat_fetch", lambda: down)
    monkeypatch.setattr(
        tasks, "load_eurostat_dataset", tasks.load_eurostat_dataset.with_options(retries=0)
    )

    with pytest.raises(RuntimeError, match=r"2 load\(s\) failed.*eurostat/nrg_pc_204"):
        daily_refresh(zones=["ES"], trailing_days=1)

    prices = (
        duckdb.connect(database)
        .execute("select count(*) from raw.entsoe_day_ahead_prices")
        .fetchone()[0]
    )
    assert prices == 100
    assert ["build", "--target", "dev"] in dbt_calls
