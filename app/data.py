"""Warehouse access for the app: one cached connection, cached queries.

The app only reads materialised marts (CLAUDE.md, stack). WAREHOUSE_PATH picks
the database: `md:power_prod` in production, any DuckDB file for local QA.
On Streamlit Community Cloud, root-level secrets are exposed as environment
variables, so MOTHERDUCK_TOKEN comes from the app's secrets there.
"""

from datetime import date
import os

import duckdb
import pandas as pd
import streamlit as st

DEFAULT_WAREHOUSE = "md:power_prod"
CACHE_TTL_S = 600  # the pipeline runs daily; ten minutes of staleness is harmless


@st.cache_resource
def connection() -> duckdb.DuckDBPyConnection:
    path = os.getenv("WAREHOUSE_PATH", DEFAULT_WAREHOUSE)
    config = {}
    if path.startswith("md:"):
        token = os.getenv("MOTHERDUCK_TOKEN")
        if not token:
            raise RuntimeError("MOTHERDUCK_TOKEN is not set")
        config["motherduck_token"] = token
    con = duckdb.connect(path, read_only=not path.startswith("md:"), config=config)
    # Same rule as the dbt profiles: never let the server's zone leak in.
    con.execute("set TimeZone = 'UTC'")
    return con


def _query(sql: str, params: list | None = None) -> pd.DataFrame:
    return connection().execute(sql, params or []).df()


@st.cache_data(ttl=CACHE_TTL_S)
def zone_codes() -> list[str]:
    """Zones with prices in the warehouse; empty before the first pipeline run."""
    try:
        zones = _query("select distinct zone_code from marts.fct_day_ahead_price_hourly")
    except duckdb.CatalogException:
        # The marts do not exist until the first pipeline run has built them.
        return []
    return sorted(zones["zone_code"])


@st.cache_data(ttl=CACHE_TTL_S)
def date_bounds(zone_code: str) -> tuple[date, date, date] | None:
    """First day, last day, and last complete day of the zone's prices.

    The last day can be short of hours: SDAC sets prices per market day in
    Brussels time, so a zone on another clock (Portugal) gets its latest
    local day one hour short until the next auction.
    """
    row = _query(
        """
        with days as (
            select delivery_date_local,
                   count(distinct hour_start_utc) = any_value(hours_in_delivery_day) as complete
            from marts.fct_day_ahead_price_hourly
            where zone_code = ?
            group by delivery_date_local
        )
        select min(delivery_date_local), max(delivery_date_local),
               max(delivery_date_local) filter (where complete)
        from days
        """,
        [zone_code],
    ).iloc[0]
    if pd.isna(row.iloc[0]):
        return None
    first, last, complete = (pd.Timestamp(value).date() for value in row)
    return first, last, complete


@st.cache_data(ttl=CACHE_TTL_S)
def hourly_prices(zone_code: str, start: date, end: date) -> pd.DataFrame:
    return _query(
        """
        select hour_start_utc, delivery_date_local, delivery_hour_local, utc_offset_minutes,
               price_eur_mwh, has_price_out_of_range, loaded_at
        from marts.fct_day_ahead_price_hourly
        where zone_code = ? and delivery_date_local between ? and ?
        order by hour_start_utc
        """,
        [zone_code, start, end],
    )


@st.cache_data(ttl=CACHE_TTL_S)
def hourly_generation(zone_code: str, start: date, end: date) -> pd.DataFrame:
    return _query(
        """
        select hour_start_utc, utc_offset_minutes, psr_type, psr_name, generation_mw
        from marts.fct_generation_hourly
        where zone_code = ? and delivery_date_local between ? and ?
            and generation_mw is not null
        order by hour_start_utc
        """,
        [zone_code, start, end],
    )
