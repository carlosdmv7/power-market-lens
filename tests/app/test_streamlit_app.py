"""Render the page with Streamlit's AppTest against a small DuckDB warehouse.

The page catches nothing itself, but asserting on `at.error` as well as
`at.exception` keeps a page that renders an error box from passing as healthy.
"""

from datetime import date
import importlib
import json
from pathlib import Path

import duckdb
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[2] / "app" / "streamlit_app.py"


def add_interchange(path: Path) -> None:
    """ES exports to FR all day; FR also seen from its own side (a modelled pair)."""
    con = duckdb.connect(path)
    con.execute(
        """
        create table marts.fct_interchange_hourly as
        select zone_code, counterpart_code, true as is_counterpart_modelled,
               date '2026-10-16' as delivery_date_local, net as net_import_mw
        from (values ('ES', 'FR', -1200.0), ('FR', 'ES', 1200.0)) as v(zone_code, counterpart_code, net)
        cross join range(24)
        """
    )
    con.close()


def add_households(path: Path) -> None:
    """Two half-years and two bands for Spain, Germany and the Netherlands.

    Band DC (typical): the Dutch taxes and levies are negative, the price is
    not. Band DA: the Dutch refund exceeds the bill, the price is negative.
    Values are exact in binary floating point.
    """
    con = duckdb.connect(path)
    con.execute(
        """
        create table marts.fct_household_price_halfyearly as
        select country_code, country_name, period_code, cast(period_start as date) as period_start_date,
               band_code, band_label, ex as price_excl_taxes_eur_kwh,
               tl as taxes_and_levies_eur_kwh, vat as vat_eur_kwh,
               ex + tl + vat as price_incl_all_taxes_eur_kwh,
               timestamptz '2026-10-19 21:00:00+00' as source_updated_at
        from (
            values ('ES', 'Spain', 'DC', '2,500 to 4,999 kWh a year', 0.1875, 0.03125, 0.03125),
                   ('DE', 'Germany', 'DC', '2,500 to 4,999 kWh a year', 0.25, 0.0625, 0.0625),
                   ('NL', 'Netherlands', 'DC', '2,500 to 4,999 kWh a year', 0.3125, -0.0625, 0.03125),
                   ('ES', 'Spain', 'DA', 'Under 1,000 kWh a year', 0.5, 0.0625, 0.0625),
                   ('DE', 'Germany', 'DA', 'Under 1,000 kWh a year', 0.5, 0.125, 0.125),
                   ('NL', 'Netherlands', 'DA', 'Under 1,000 kWh a year', 0.5, -0.75, -0.03125)
        ) as v(country_code, country_name, band_code, band_label, ex, tl, vat)
        cross join (
            values ('2025-S2', '2025-07-01'), ('2026-S1', '2026-01-01')
        ) as p(period_code, period_start)
        """
    )
    con.execute(
        """
        create table marts.fct_household_price_components_yearly as
        select country_code, country_name, 2025 as year, 'DC' as band_code, component,
               value as component_eur_kwh,
               value / sum(value) over (partition by country_code) as share_of_total
        from (
            values ('ES', 'Spain', 'energy_and_supply', 0.125), ('ES', 'Spain', 'network', 0.0625),
                   ('ES', 'Spain', 'taxes_and_levies', 0.0625), ('ES', 'Spain', 'vat', 0.0625),
                   ('NL', 'Netherlands', 'energy_and_supply', 0.125),
                   ('NL', 'Netherlands', 'network', 0.125),
                   ('NL', 'Netherlands', 'taxes_and_levies', -0.015625),
                   ('NL', 'Netherlands', 'vat', 0.046875)
        ) as v(country_code, country_name, component, value)
        """
    )
    con.close()


def add_monitor(path: Path) -> None:
    """The monitor's table: every priced hour unremarkable, except a three-hour
    episode in Spain (16 Oct, 06:00–08:00 UTC) when nuclear dropped."""
    con = duckdb.connect(path)
    con.execute("create schema monitor")
    con.execute(
        """
        create table monitor.system_conditions_hourly as
        select zone_code, hour_start_utc, delivery_date_local, utc_offset_minutes,
               case when unusual then 2.5 + hour(hour_start_utc at time zone 'UTC') - 6
                    else 0.3 end as unusualness,
               unusual as is_unusual, 'T2' as statistic,
               case when unusual then 'nuclear_mw' else 'wind_mw' end as driver_1,
               case when unusual then -8.0 else 0.5 end as driver_1_z,
               'forecast_error_mw' as driver_2, 3.0 as driver_2_z,
               'price_eur_mwh' as driver_3, 0.2 as driver_3_z
        from (
            select *, zone_code = 'ES'
                and hour_start_utc between timestamptz '2026-10-16 06:00:00+00'
                and timestamptz '2026-10-16 08:00:00+00' as unusual
            from marts.fct_day_ahead_price_hourly
        )
        """
    )
    con.close()


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
               g.psr_name, g.display_group, g.mw as generation_mw
        from marts.fct_day_ahead_price_hourly as p
        cross join (
            values ('B16', 'Solar', 'Solar', 9000.0), ('B19', 'Wind Onshore', 'Wind', 7000.0),
                   ('B14', 'Nuclear', 'Nuclear', 6000.0),
                   ('B11', 'Hydro Run-of-river and poundage', 'Hydro & other', 500.0)
        ) as g(psr_type, psr_name, display_group, mw)
        -- Actual generation stops at the latest published hour; prices run on.
        where p.hour_start_utc <= timestamptz '2026-10-17 12:00:00+00'
        """
    )
    # Load: Spain's forecast within 1% of the actual; DE-LU's 15% above it,
    # steadily (the shape NL has on real data).
    con.execute(
        """
        create table marts.fct_load_hourly as
        select zone_code, hour_start_utc, delivery_date_local, utc_offset_minutes,
               case when hour_start_utc <= timestamptz '2026-10-17 12:00:00+00'
                    then 30000.0 end as actual_load_mw,
               case when zone_code = 'DE-LU' then 34500.0 else 30300.0 end as forecast_load_mw,
               forecast_load_mw - actual_load_mw as forecast_error_mw
        from marts.fct_day_ahead_price_hourly
        """
    )
    # What dbt derives from the rows above (ADR-004): 16,500 of 22,500 MW
    # renewable, all of it low-carbon, 26.36 g/kWh; DE-LU with a lignite-like
    # proxy share, so its caption has something to say.
    con.execute(
        """
        create table marts.fct_generation_mix_hourly as
        select zone_code, hour_start_utc, delivery_date_local, utc_offset_minutes,
               sum(generation_mw) as production_mw,
               16500 / 22500 as renewable_share,
               1.0 as low_carbon_share,
               593000 / 22500 as carbon_intensity_g_per_kwh,
               case when zone_code = 'DE-LU' then 0.23 else 0.0 end as proxy_factor_share
        from marts.fct_generation_hourly
        group by all
        """
    )
    con.close()


@pytest.fixture
def run_page(tmp_path, monkeypatch):
    def run(
        with_data: bool | None,
        *,
        zone: str | None = None,
        page: str | None = None,
        interchange: bool = False,
        noisy_load: bool = False,
        households: bool = False,
        monitor: bool = False,
        query: dict[str, str] | None = None,
    ) -> AppTest:
        warehouse = (
            tmp_path
            / f"{with_data}-{zone}-{interchange}-{noisy_load}-{households}-{monitor}.duckdb"
        )
        make_warehouse(warehouse, with_data)
        if interchange:
            add_interchange(warehouse)
        if households:
            add_households(warehouse)
        if monitor:
            add_monitor(warehouse)
        if noisy_load:
            # DE-LU's forecast alternates 15% above and below the actual.
            con = duckdb.connect(warehouse)
            con.execute(
                "update marts.fct_load_hourly set "
                "forecast_error_mw = case when hour(hour_start_utc) % 2 = 0 "
                "then 4500.0 else -4500.0 end where zone_code = 'DE-LU'"
            )
            con.close()
        monkeypatch.setenv("WAREHOUSE_PATH", str(warehouse))
        # The connection is a cached resource that lives as long as the process;
        # without this, a second test would read the first test's warehouse.
        st.cache_resource.clear()
        st.cache_data.clear()
        at = AppTest.from_file(str(APP), default_timeout=30)
        if zone is not None:
            at.query_params["zone"] = zone
        for key, value in (query or {}).items():
            at.query_params[key] = value
        at.run()
        if page is not None:
            at.switch_page(page).run()
        return at

    return run


def test_page_renders_metrics_and_all_four_charts(run_page):
    at = run_page(with_data=True)

    assert not at.exception
    assert not at.error
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Hours at or below zero"] == "7"  # 13:00 UTC on each of 7 days
    assert metrics["Cheapest hour"] == "-1.00 €/MWh"
    assert metrics["Renewable share"] == "73%"
    assert metrics["Low-carbon share"] == "100%"
    assert metrics["Carbon intensity"] == "26 g/kWh"
    assert len(at.get("vega_lite_chart")) == 4  # price, mix, load, carbon intensity
    assert metrics["Average load"] == "30,000 MW"
    assert any("off by 1.0% on average" in c.value for c in at.caption)
    assert not any("do not cover the same consumption" in c.value for c in at.caption)
    assert any("Renewables made 73% of it, at 26 gCO₂eq per kWh" in m.value for m in at.markdown)
    assert not any("stated proxy" in c.value for c in at.caption)
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


def test_an_unreachable_warehouse_shows_a_message_not_a_traceback(tmp_path, monkeypatch):
    # A path that does not exist: opening it read-only fails like a warehouse
    # that is down or a token that is no longer accepted.
    monkeypatch.setenv("WAREHOUSE_PATH", str(tmp_path / "missing" / "warehouse.duckdb"))
    st.cache_resource.clear()
    st.cache_data.clear()

    at = AppTest.from_file(str(APP), default_timeout=30).run()

    assert not at.exception
    assert [e.value for e in at.error] == [
        "The data is temporarily unavailable. This is a problem on our side, not yours: "
        "please try again in a few minutes."
    ]
    st.cache_resource.clear()


@pytest.mark.parametrize("page", [None, "views/map.py"])
def test_stale_data_is_announced_above_the_charts(run_page, page):
    # The fixture's last load is 17 October 2026: long past the 36-hour limit.
    at = run_page(with_data=True, page=page)

    assert not at.exception
    assert any(
        "out of date: the last load was on 17 Oct 2026 at 13:30 UTC" in w.value for w in at.warning
    )


def test_map_page_without_the_interchange_mart_draws_no_flows(run_page):
    at = run_page(with_data=True, page="views/map.py")

    assert not at.exception
    assert not at.toggle  # nothing to toggle
    assert not any("Arrows show" in c.value for c in at.caption)


def _map_layers(at: AppTest) -> int:
    return len(json.loads(at.get("vega_lite_chart")[0].proto.spec)["layer"])


def test_map_page_draws_flows_over_the_prices(run_page):
    at = run_page(with_data=True, page="views/map.py", interchange=True)

    assert not at.exception
    assert at.toggle[0].value is True
    assert any("Arrows show" in c.value for c in at.caption)
    assert _map_layers(at) == 4  # land, zones, flow lines, arrowheads


def test_map_flows_toggle_off_removes_the_arrows(run_page):
    at = run_page(with_data=True, page="views/map.py", interchange=True)
    at.toggle[0].set_value(False).run()

    assert _map_layers(at) == 2


def test_a_border_seen_from_both_sides_is_one_flow_towards_the_importer(tmp_path, monkeypatch):
    warehouse = tmp_path / "flows.duckdb"
    make_warehouse(warehouse, with_data=True)
    add_interchange(warehouse)
    monkeypatch.setenv("WAREHOUSE_PATH", str(warehouse))
    monkeypatch.syspath_prepend(str(APP.parent))
    st.cache_resource.clear()
    st.cache_data.clear()
    # The app's modules import each other by bare name, from app/.
    data = importlib.import_module("data")

    flows = data.daily_net_flows(date(2026, 10, 16))

    assert flows[["source", "target", "net_mw"]].values.tolist() == [["ES", "FR", 1200.0]]


def test_a_zone_leaning_on_proxy_factors_says_its_intensity_is_approximate(run_page):
    at = run_page(with_data=True, zone="DE-LU")

    assert any("23% of this zone's production" in c.value for c in at.caption)


def test_a_steady_load_forecast_gap_is_called_a_definition_gap(run_page):
    at = run_page(with_data=True, zone="DE-LU")

    assert any(
        "forecast sits 15% above the actual" in c.value
        and "do not cover the same consumption" in c.value
        for c in at.caption
    )


def test_a_large_but_two_sided_forecast_miss_is_not_a_definition_gap(run_page):
    at = run_page(with_data=True, zone="DE-LU", noisy_load=True)

    assert not any("do not cover the same consumption" in c.value for c in at.caption)


def test_unusual_hours_are_drawn_and_grouped_into_episodes(run_page):
    at = run_page(with_data=True, monitor=True)

    assert not at.exception
    assert not at.error
    assert len(at.get("vega_lite_chart")) == 5  # price, mix, load, intensity, unusual hours
    assert any("not a reliability or security" in c.value for c in at.caption)
    episodes = at.dataframe[0].value
    assert len(episodes) == 1  # three consecutive hours, one episode
    assert episodes["Hours"].iloc[0] == 3
    assert episodes["Peak (× limit)"].iloc[0] == 4.5
    assert episodes["What stood out at the peak"].iloc[0] == (
        "Nuclear far below usual, load forecast error above usual"
    )


def test_a_zone_with_no_unusual_hour_says_so(run_page):
    at = run_page(with_data=True, monitor=True, zone="PT")

    assert any("No hour in these dates was beyond the control limit" in c.value for c in at.caption)


def test_without_the_monitor_table_the_section_is_left_out(run_page):
    at = run_page(with_data=True)

    assert not any("UNUSUAL HOURS" in m.value for m in at.markdown)
    assert len(at.get("vega_lite_chart")) == 4


HOUSEHOLDS = "views/households.py"


def test_households_page_answers_for_the_typical_household(run_page):
    at = run_page(with_data=True, households=True, page=HOUSEHOLDS)

    assert not at.exception
    assert not at.error
    assert at.selectbox[0].value == "DC"
    assert len(at.get("vega_lite_chart")) == 3  # split by taxes, history, components
    assert any(
        "In Jan–Jun 2026, a household using 2,500 to 4,999 kWh a year paid between "
        "0.250 €/kWh in Spain and 0.375 €/kWh in Germany" in m.value
        for m in at.markdown
    )
    # Dutch taxes are negative at this band; the price is not.
    assert any("Dutch taxes and levies are below zero" in m.value for m in at.markdown)
    assert not any("Dutch price is below zero" in m.value for m in at.markdown)
    assert any("Latest half-year" in m.value and "Jan–Jun 2026" in m.value for m in at.markdown)
    assert not any("Below zero:" in c.value for c in at.caption)


def test_households_page_explains_a_negative_dutch_price(run_page):
    at = run_page(with_data=True, households=True, page=HOUSEHOLDS, query={"band": "DA"})

    assert not at.exception
    assert at.selectbox[0].value == "DA"
    assert any(
        "between -0.281 €/kWh in the Netherlands and 0.750 €/kWh in Germany" in m.value
        for m in at.markdown
    )
    assert any("The Dutch price is below zero" in m.value for m in at.markdown)
    assert any(
        "Below zero: Netherlands, as low as -0.281 €/kWh in Jul–Dec 2025" in c.value
        for c in at.caption
    )
    # No yearly components for this band in the fixture: that section is left out.
    assert len(at.get("vega_lite_chart")) == 2


def test_households_page_before_the_marts_exist_explains_itself(run_page):
    at = run_page(with_data=True, page=HOUSEHOLDS)

    assert not at.exception
    assert not at.error
    assert "Household prices are not in the warehouse yet" in at.info[0].value
