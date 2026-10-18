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
def date_bounds(zone_code: str) -> tuple[date, date] | None:
    try:
        row = _query(
            "select min(delivery_date_local), max(delivery_date_local) "
            "from marts.fct_day_ahead_price_hourly where zone_code = ?",
            [zone_code],
        ).iloc[0]
    except duckdb.CatalogException:
        # The marts do not exist until the first pipeline run has built them.
        return None
    if pd.isna(row.iloc[0]):
        return None
    return pd.Timestamp(row.iloc[0]).date(), pd.Timestamp(row.iloc[1]).date()


@st.cache_data(ttl=CACHE_TTL_S)
def hourly_prices(zone_code: str, start: date, end: date) -> pd.DataFrame:
    return _query(
        """
        select hour_start_utc, delivery_date_local, delivery_hour_local,
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
        select hour_start_utc, psr_type, psr_name, generation_mw
        from marts.fct_generation_hourly
        where zone_code = ? and delivery_date_local between ? and ?
            and generation_mw is not null
        order by hour_start_utc
        """,
        [zone_code, start, end],
    )
