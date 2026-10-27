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
    # France has a row but no prices: the selector must leave it out.
    con.execute(
        """
        create table marts.dim_bidding_zone as
        select * from (
            values ('DE-LU', 'Germany–Luxembourg', 'Germany'), ('ES', 'Spain', 'Spain'),
                   ('PT', 'Portugal', 'Portugal'), ('FR', 'France', 'France')
        ) as z(zone_code, zone_name, country_name)
        """
    )
    con.execute(
        """
        create table marts.fct_day_ahead_price_hourly as
        select
            zone_code,
            ts as hour_start_utc,
            cast(ts at time zone tz as date) as delivery_date_local,
            hour(ts at time zone tz) as delivery_hour_local,
            cast((epoch(ts at time zone tz) - epoch(ts)) / 60 as integer) as utc_offset_minutes,
            24 as hours_in_delivery_day,  -- no DST change in the window
            case when hour(ts) = 13 then -1.0 else 40 + hour(ts) end as price_eur_mwh,
            false as has_price_out_of_range,
            timestamptz '2026-10-17 13:30:00+00' as loaded_at
        -- Brussels market days 11–17 Oct. In Lisbon (an hour behind) that is
        -- 23:00 on the 10th to 22:00 on the 17th: the last local day is short.
        from generate_series(
            timestamptz '2026-10-10 22:00:00+00', timestamptz '2026-10-17 21:00:00+00', interval 1 hour
        ) as t(ts)
        -- DE-LU sorts first: the fallback must still be Spain, not the first zone.
        cross join (
            values ('DE-LU', 'Europe/Berlin'), ('ES', 'Europe/Madrid'), ('PT', 'Europe/Lisbon')
        ) as z(zone_code, tz)
        """
        + ("" if with_data else " where false")
    )
    con.execute(
        """
        create table marts.fct_generation_hourly as
        select p.zone_code, p.hour_start_utc, p.delivery_date_local, p.utc_offset_minutes, g.psr_type,
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
    def run(with_data: bool | None, zone: str | None = None, page: str | None = None) -> AppTest:
        warehouse = tmp_path / f"{with_data}-{zone}.duckdb"
        make_warehouse(warehouse, with_data)
        monkeypatch.setenv("WAREHOUSE_PATH", str(warehouse))
        # The connection is a cached resource that lives as long as the process;
        # without this, a second test would read the first test's warehouse.
        st.cache_resource.clear()
        st.cache_data.clear()
        at = AppTest.from_file(str(APP), default_timeout=30)
        if zone is not None:
            at.query_params["zone"] = zone
        at.run()
        if page is not None:
            at.switch_page(page).run()
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


def test_the_zone_comes_from_the_url_and_stays_in_it(run_page):
    at = run_page(with_data=True, zone="PT")

    assert not at.exception
    assert at.selectbox[0].value == "PT"
    # Names on screen, ordered by country; codes stay the value and the URL.
    assert at.selectbox[0].options == ["Germany–Luxembourg", "Portugal", "Spain"]
    assert at.query_params["zone"] == "PT"
    assert at.title[0].value.startswith("Portugal: day-ahead price")
    # 12:00 UTC is 13:00 in Lisbon: the local clock is the zone's own.
    assert any("only to Sat 17 Oct, 13:00" in c.value for c in at.caption)


def test_a_short_last_day_is_not_called_complete(run_page):
    at = run_page(with_data=True, zone="PT")
    spain = run_page(with_data=True)

    assert "**:color[16 Oct 2026]" in _strip(at)
    assert any("17 Oct is one hour short" in c.value for c in at.caption)
    assert "**:color[17 Oct 2026]" in _strip(spain)
    assert not any("one hour short" in c.value for c in spain.caption)


def test_an_unknown_zone_in_the_url_falls_back_to_spain(run_page):
    at = run_page(with_data=True, zone="XX")

    assert not at.exception
    assert at.selectbox[0].value == "ES"


def _strip(at: AppTest) -> str:
    """The header's freshness strip: label and value are separate colour marks."""
    return next(m.value for m in at.markdown if "Complete through" in m.value)


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


def test_map_page_colours_each_zone_and_ranks_them(run_page):
    at = run_page(with_data=True, page="views/map.py")

    assert not at.exception
    assert not at.error
    assert len(at.get("vega_lite_chart")) == 2  # the map and the ranking
    # 17 Oct is the last day complete everywhere: Portugal's local 17th is short.
    assert at.date_input[0].value.isoformat() == "2026-10-16"
    assert any("Zones" in m.value and "3 priced on this day" in m.value for m in at.markdown)
    # The fixture prices every zone the same over its local day.
    assert any("every zone cleared at the same price" in m.value for m in at.markdown)


def test_map_page_on_an_empty_warehouse_explains_itself(run_page):
    at = run_page(with_data=None, page="views/map.py")

    assert not at.exception
    assert "No data in the warehouse yet" in at.info[0].value
