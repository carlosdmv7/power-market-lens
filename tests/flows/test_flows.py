"""The flows end to end against a temporary local Prefect server, with ENTSO-E
replaced by recorded fixtures, MotherDuck by a DuckDB file and dbt by a stub."""

from datetime import date
from pathlib import Path

import duckdb
from prefect.testing.utilities import prefect_test_harness
import pytest

from power_market_lens.flows import tasks
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

    counts = daily_refresh(trailing_days=7)

    assert counts == {"ES/day_ahead_prices": 100, "ES/generation_per_type": 7}
    assert dbt_calls == [["deps"], ["build", "--target", "dev"]]
    run_ids = (
        duckdb.connect(database)
        .execute("select distinct _run_id from raw.entsoe_day_ahead_prices")
        .fetchall()
    )
    assert len(run_ids) == 1
    assert run_ids[0][0] != "manual"  # the Prefect flow-run id


def test_backfill_twice_appends_twice(wired):
    database, _ = wired

    backfill(date(2025, 10, 25), date(2025, 10, 26))
    backfill(date(2025, 10, 25), date(2025, 10, 26))

    total, distinct_points = (
        duckdb.connect(database)
        .execute(
            "select count(*), count(distinct period_start_utc) from raw.entsoe_day_ahead_prices"
        )
        .fetchone()
    )
    assert (total, distinct_points) == (200, 100)


def test_a_failed_dbt_build_fails_the_flow(wired, monkeypatch):
    monkeypatch.setattr(tasks, "run_dbt", lambda args: args == ["deps"])

    with pytest.raises(RuntimeError, match="dbt build failed"):
        daily_refresh(trailing_days=1)


def test_run_dbt_reports_the_cli_exit_status():
    # The real CLI, in its own process; neither command touches a database.
    assert tasks.run_dbt(["debug", "--config-dir"]) is True
    assert tasks.run_dbt(["no-such-command"]) is False
