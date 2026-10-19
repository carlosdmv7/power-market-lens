"""Render the page with Streamlit's AppTest against a small DuckDB warehouse.

The page catches nothing itself, but asserting on `at.error` as well as
`at.exception` keeps a page that renders an error box from passing as healthy.
"""

from pathlib import Path

import duckdb
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[2] / "app" / "streamlit_app.py"


def make_warehouse(path: Path, with_data: bool | None) -> None:
    """with_data None: a database where dbt has never built the marts."""
    con = duckdb.connect(path)
    if with_data is None:
        con.close()
        return
    con.execute("set TimeZone = 'UTC'")
    con.execute("create schema marts")
    con.execute(
        """
        create table marts.fct_day_ahead_price_hourly as
        select
            'ES' as zone_code,
            ts as hour_start_utc,
            cast(ts at time zone 'Europe/Madrid' as date) as delivery_date_local,
            hour(ts at time zone 'Europe/Madrid') as delivery_hour_local,
            case when hour(ts) = 13 then -1.0 else 40 + hour(ts) end as price_eur_mwh,
            false as has_price_out_of_range,
            timestamptz '2026-10-17 13:30:00+00' as loaded_at
        from generate_series(
            timestamptz '2026-10-10 22:00:00+00', timestamptz '2026-10-17 21:00:00+00', interval 1 hour
        ) as t(ts)
        """
        + ("" if with_data else " where false")
    )
    con.execute(
        """
        create table marts.fct_generation_hourly as
        select p.zone_code, p.hour_start_utc, p.delivery_date_local, g.psr_type,
               g.psr_name, g.mw as generation_mw
        from marts.fct_day_ahead_price_hourly as p
        cross join (
            values ('B16', 'Solar', 9000.0), ('B19', 'Wind Onshore', 7000.0),
                   ('B14', 'Nuclear', 6000.0), ('B11', 'Hydro Run-of-river and poundage', 500.0)
        ) as g(psr_type, psr_name, mw)
        -- Actual generation stops at the latest published hour; prices run on.
        where p.hour_start_utc <= timestamptz '2026-10-17 12:00:00+00'
        """
    )
    con.close()


@pytest.fixture
def run_page(tmp_path, monkeypatch):
    def run(with_data: bool | None) -> AppTest:
        warehouse = tmp_path / f"{with_data}.duckdb"
        make_warehouse(warehouse, with_data)
        monkeypatch.setenv("WAREHOUSE_PATH", str(warehouse))
        # The connection is a cached resource that lives as long as the process;
        # without this, a second test would read the first test's warehouse.
        st.cache_resource.clear()
        st.cache_data.clear()
        at = AppTest.from_file(str(APP), default_timeout=30)
        at.run()
        return at

    return run


def test_page_renders_metrics_and_both_charts(run_page):
    at = run_page(with_data=True)

    assert not at.exception
    assert not at.error
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Hours at or below zero"] == "7"  # 13:00 UTC on each of 7 days
    assert metrics["Cheapest hour"] == "-1.00 €/MWh"
    assert len(at.get("vega_lite_chart")) == 2
    assert any("price averaged 49.25 €/MWh" in m.value for m in at.markdown)  # the lede
    # 12:00 UTC is 14:00 in Madrid.
    assert any("only to Sat 17 Oct, 14:00" in c.value for c in at.caption)


def test_empty_warehouse_explains_itself(run_page):
    at = run_page(with_data=False)

    assert not at.exception
    assert not at.error
    assert "No data in the warehouse yet" in at.info[0].value


def test_warehouse_without_marts_explains_itself(run_page):
    # power_prod before its first pipeline run: no marts schema at all.
    at = run_page(with_data=None)

    assert not at.exception
    assert not at.error
    assert "No data in the warehouse yet" in at.info[0].value
