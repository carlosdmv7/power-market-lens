"""The monitor job against a small DuckDB holding the four marts it reads."""

import duckdb
import numpy as np
import pandas as pd
import pytest

from power_market_lens.monitor import job

GROUPS = {
    "wind_mw": "Wind",
    "solar_mw": "Solar",
    "nuclear_mw": "Nuclear",
    "fossil_mw": "Fossil",
    "hydro_other_mw": "Hydro & other",
}


def build_marts(con: duckdb.DuckDBPyConnection, zones: pd.DataFrame) -> None:
    """The marts' columns the job reads, from synthetic zones.

    Generation is stored long, as in fct_generation_hourly (two production
    types for wind, to check they are summed); net imports as two borders.
    """
    con.execute("set TimeZone = 'UTC'")
    con.execute("create schema marts")
    con.register("zones", zones)
    con.execute(
        """
        create table marts.fct_load_hourly as
        select zone_code, hour_start_utc, cast(delivery_date_local as date) as delivery_date_local,
               delivery_hour_local, utc_offset_minutes, actual_load_mw, forecast_error_mw
        from zones
        """
    )
    unions = " union all ".join(
        f"select zone_code, hour_start_utc, '{label}' as display_group, {column} as generation_mw "
        "from zones"
        if column != "wind_mw"
        else "select zone_code, hour_start_utc, 'Wind' as display_group, "
        "wind_mw * 0.75 as generation_mw from zones "
        "union all select zone_code, hour_start_utc, 'Wind', wind_mw * 0.25 from zones"
        for column, label in GROUPS.items()
    )
    con.execute(f"create table marts.fct_generation_hourly as {unions}")  # noqa: S608
    con.execute(
        """
        create table marts.fct_interchange_hourly as
        select zone_code, hour_start_utc, net_import_mw * 0.5 as net_import_mw from zones
        union all
        select zone_code, hour_start_utc, net_import_mw * 0.5 from zones
        """
    )
    con.execute(
        """
        create table marts.fct_day_ahead_price_hourly as
        select zone_code, hour_start_utc, price_eur_mwh from zones
        """
    )
    con.unregister("zones")


@pytest.fixture(scope="module")
def con(make_zone):
    connection = duckdb.connect()
    zones = pd.concat([make_zone("ES", seed=1), make_zone("PT", seed=2)])
    build_marts(connection, zones)
    yield connection, zones
    connection.close()


def test_features_come_back_one_row_per_zone_and_hour_with_the_groups_summed(con):
    connection, zones = con

    features = job.read_features(connection)

    assert len(features) == len(zones)
    expected = zones.sort_values(["zone_code", "hour_start_utc"]).reset_index(drop=True)
    np.testing.assert_allclose(features["wind_mw"], expected["wind_mw"])
    np.testing.assert_allclose(features["net_import_mw"], expected["net_import_mw"])


def test_run_writes_a_typed_table_and_a_second_run_replaces_it(con):
    connection, _ = con

    first = job.run(connection)
    second = job.run(connection)

    count, zones_scored = connection.execute(
        f"select count(*), count(distinct zone_code) from {job.TABLE}"
    ).fetchone()
    assert first == second == count  # replaced, not appended
    assert zones_scored == 2
    types = dict(
        connection.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema = 'monitor' and table_name = 'system_conditions_hourly'"
        ).fetchall()
    )
    assert types["hour_start_utc"] == "TIMESTAMP WITH TIME ZONE"
    assert types["delivery_date_local"] == "DATE"
    assert types["is_unusual"] == "BOOLEAN"
    assert types["driver_1"] == "VARCHAR"
    keys = connection.execute(
        f"select count(*) = count(distinct (zone_code, hour_start_utc)) from {job.TABLE}"
    ).fetchone()[0]
    assert keys


def test_a_warehouse_without_enough_history_leaves_the_table_alone(make_zone):
    connection = duckdb.connect()
    short = make_zone("ES", end="2024-02-15 23:00")
    build_marts(connection, short)

    assert job.run(connection) == 0
    assert not connection.execute(
        "select count(*) from information_schema.tables where table_schema = 'monitor'"
    ).fetchone()[0]
