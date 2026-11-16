"""Read the monitor's variables from the marts, score every hour, write the result.

    python -m power_market_lens.monitor.job      # APP_ENV picks the database

Run by the Pipeline after `dbt build` (ADR-008). The whole history is scored
again on every run and the table replaced in one statement. That keeps the
result a pure function of the marts, with no state to drift, and costs one
scan of four marts and a few seconds of numpy.
"""

import logging

import duckdb
import pandas as pd

from power_market_lens.config import load_settings
from power_market_lens.ingest.warehouse import connect
from power_market_lens.monitor.conditions import TOP_DRIVERS, assess

log = logging.getLogger(__name__)

SCHEMA = "monitor"
TABLE = f"{SCHEMA}.system_conditions_hourly"

# One row per zone and hour that has load, generation, flows and a price.
# Generation is pivoted to the app's five groups, flows summed over borders.
FEATURES_SQL = """
with load as (
    select zone_code, hour_start_utc, delivery_date_local, delivery_hour_local,
           utc_offset_minutes, actual_load_mw, forecast_error_mw
    from marts.fct_load_hourly
),
generation as (
    select zone_code, hour_start_utc,
           sum(generation_mw) filter (where display_group = 'Wind') as wind_mw,
           sum(generation_mw) filter (where display_group = 'Solar') as solar_mw,
           sum(generation_mw) filter (where display_group = 'Nuclear') as nuclear_mw,
           sum(generation_mw) filter (where display_group = 'Fossil') as fossil_mw,
           sum(generation_mw) filter (where display_group = 'Hydro & other') as hydro_other_mw
    from marts.fct_generation_hourly
    group by zone_code, hour_start_utc
),
flows as (
    select zone_code, hour_start_utc, sum(net_import_mw) as net_import_mw
    from marts.fct_interchange_hourly
    group by zone_code, hour_start_utc
),
prices as (
    select zone_code, hour_start_utc, price_eur_mwh
    from marts.fct_day_ahead_price_hourly
)
select *
from load
inner join generation using (zone_code, hour_start_utc)
inner join flows using (zone_code, hour_start_utc)
inner join prices using (zone_code, hour_start_utc)
order by zone_code, hour_start_utc
"""


def read_features(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(FEATURES_SQL).df()


def write(con: duckdb.DuckDBPyConnection, result: pd.DataFrame) -> int:
    """Replace the monitor table with `result` in one statement; return its rows.

    Types are cast explicitly, so the table does not depend on what pandas
    inferred: a reader (the app, a dbt source test) can rely on them.
    """
    drivers = ",\n".join(
        f"cast(driver_{n} as varchar) as driver_{n}, "
        f"cast(driver_{n}_z as double) as driver_{n}_z, "
        f"cast(driver_{n}_share as double) as driver_{n}_share"
        for n in range(1, TOP_DRIVERS + 1)
    )
    con.execute(f"create schema if not exists {SCHEMA}")
    con.register("_result", result)
    try:
        con.execute(
            f"""
            create or replace table {TABLE} as
            select
                cast(zone_code as varchar) as zone_code,
                cast(hour_start_utc as timestamptz) as hour_start_utc,
                cast(delivery_date_local as date) as delivery_date_local,
                cast(delivery_hour_local as integer) as delivery_hour_local,
                cast(utc_offset_minutes as integer) as utc_offset_minutes,
                cast(is_reference as boolean) as is_reference,
                cast(t2 as double) as t2,
                cast(q as double) as q,
                cast(t2_limit as double) as t2_limit,
                cast(q_limit as double) as q_limit,
                cast(unusualness as double) as unusualness,
                cast(is_unusual as boolean) as is_unusual,
                cast(statistic as varchar) as statistic,
                {drivers},
                cast(n_components as integer) as n_components,
                cast(n_features as integer) as n_features,
                now() as assessed_at
            from _result
            """  # noqa: S608 - no user input; column names are fixed above
        )
    finally:
        con.unregister("_result")
    return len(result)


def run(con: duckdb.DuckDBPyConnection) -> int:
    """Score every zone's history and replace the table; return the rows written."""
    try:
        features = read_features(con)
    except duckdb.CatalogException:
        log.warning("The marts are not built yet; nothing for the monitor to score")
        return 0
    result = assess(features)
    if result.empty:
        log.warning("No zone has enough history to score; %s left as it was", TABLE)
        return 0
    rows = write(con, result)
    evaluation = result[~result["is_reference"]]
    log.info(
        "%s: %d hours scored across %d zones; %.1f%% of the hours since 2025 unusual",
        TABLE,
        rows,
        result["zone_code"].nunique(),
        100 * evaluation["is_unusual"].mean() if len(evaluation) else 0.0,
    )
    return rows


if __name__ == "__main__":
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    with connect(settings) as connection:
        run(connection)
