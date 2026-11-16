"""Raw layer in MotherDuck: table definitions and append-only loads.

Raw is append-only (ADR-003): a load inserts rows stamped with the same
`_loaded_at`, its source and the window it asked for, and nothing is ever
updated or deleted. Staging keeps the latest version of each point.

Every run re-pulls a trailing week to catch revisions, and almost none of it
has changed. For the ENTSO-E tables a load therefore appends only the points
that are new, or whose values differ from the latest version already stored
(ADR-011): raw still records every revision, without one copy per pull.

`python -m power_market_lens.ingest.warehouse` creates the raw tables in the
database for APP_ENV (power_dev by default). It is idempotent.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
import logging

import duckdb
import pandas as pd
from pydantic import BaseModel

from power_market_lens.config import Settings, load_settings
from power_market_lens.ingest.models import (
    CrossBorderFlowRow,
    DayAheadPriceRow,
    GenerationRow,
    HouseholdPriceComponentRow,
    HouseholdPriceRow,
    TotalLoadRow,
)

log = logging.getLogger(__name__)

RAW_SCHEMA = "raw"

_METADATA_COLUMNS = """
    _loaded_at timestamptz not null,
    _source varchar not null,
    _window_start timestamptz not null,
    _window_end timestamptz not null,
    _run_id varchar not null
"""

# Each table's data columns mirror its row model, field for field (a test
# checks it); the load metadata columns are shared.
RAW_TABLES: dict[str, tuple[type[BaseModel], str]] = {
    "entsoe_day_ahead_prices": (
        DayAheadPriceRow,
        """
        bidding_zone_eic varchar not null,
        period_start_utc timestamptz not null,
        resolution_minutes smallint not null,
        price_eur_mwh double not null,
        currency varchar not null,
        price_unit varchar not null,
        document_mrid varchar not null,
        document_created_at timestamptz not null,
        """,
    ),
    "entsoe_generation_per_type": (
        GenerationRow,
        """
        bidding_zone_eic varchar not null,
        psr_type varchar not null,
        flow_direction varchar not null,
        period_start_utc timestamptz not null,
        resolution_minutes smallint not null,
        quantity_mw double not null,
        document_mrid varchar not null,
        document_created_at timestamptz not null,
        """,
    ),
    "entsoe_total_load": (
        TotalLoadRow,
        """
        bidding_zone_eic varchar not null,
        process_type varchar not null,
        period_start_utc timestamptz not null,
        resolution_minutes smallint not null,
        load_mw double not null,
        document_mrid varchar not null,
        document_created_at timestamptz not null,
        """,
    ),
    "entsoe_crossborder_flows": (
        CrossBorderFlowRow,
        """
        out_zone_eic varchar not null,
        in_zone_eic varchar not null,
        period_start_utc timestamptz not null,
        resolution_minutes smallint not null,
        flow_mw double not null,
        document_mrid varchar not null,
        document_created_at timestamptz not null,
        """,
    ),
    # Eurostat publishes these twice a year (prices) or once (components); a
    # load appends only when the dataset's update time has moved (ADR-010).
    "eurostat_household_prices": (
        HouseholdPriceRow,
        """
        geo varchar not null,
        time_period varchar not null,
        nrg_cons varchar not null,
        tax varchar not null,
        currency varchar not null,
        price_eur_kwh double not null,
        status_flag varchar,
        source_updated_at timestamptz not null,
        """,
    ),
    "eurostat_household_price_components": (
        HouseholdPriceComponentRow,
        """
        geo varchar not null,
        time_period varchar not null,
        nrg_cons varchar not null,
        nrg_prc varchar not null,
        currency varchar not null,
        component_eur_kwh double not null,
        status_flag varchar,
        source_updated_at timestamptz not null,
        """,
    ),
}

METADATA_FIELDS = ("_loaded_at", "_source", "_window_start", "_window_end", "_run_id")

# Per ENTSO-E table: the columns that identify a point (staging deduplicates
# on the same ones; a test keeps them equal), and the values that make a
# version of it. document_mrid and document_created_at are left out: ENTSO-E
# issues both per request, not per revision. Six pulls of the same day carried
# six ids and six creation times, and identical values (ADR-011).
POINT_KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "entsoe_day_ahead_prices": (
        ("bidding_zone_eic", "period_start_utc", "resolution_minutes"),
        ("price_eur_mwh", "currency", "price_unit"),
    ),
    "entsoe_generation_per_type": (
        (
            "bidding_zone_eic",
            "psr_type",
            "flow_direction",
            "period_start_utc",
            "resolution_minutes",
        ),
        ("quantity_mw",),
    ),
    "entsoe_total_load": (
        ("bidding_zone_eic", "process_type", "period_start_utc", "resolution_minutes"),
        ("load_mw",),
    ),
    "entsoe_crossborder_flows": (
        ("out_zone_eic", "in_zone_eic", "period_start_utc", "resolution_minutes"),
        ("flow_mw",),
    ),
}


def connect(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Open the MotherDuck database for this environment (power_dev / power_prod)."""
    if settings.motherduck_token is None:
        raise RuntimeError("MOTHERDUCK_TOKEN is not set; see .env.example")
    return duckdb.connect(
        f"md:{settings.database}",
        config={"motherduck_token": settings.motherduck_token.get_secret_value()},
    )


def ensure_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"create schema if not exists {RAW_SCHEMA}")
    for table, (_, columns) in RAW_TABLES.items():
        con.execute(
            f"create table if not exists {RAW_SCHEMA}.{table} ({columns}{_METADATA_COLUMNS})"
        )


def append_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: Sequence[BaseModel],
    *,
    source: str,
    window_start: datetime,
    window_end: datetime,
    run_id: str,
    loaded_at: datetime | None = None,
) -> int:
    """Append one batch to a raw table in a single INSERT; return the rows appended.

    For a table in POINT_KEYS, only the rows that are new or changed since
    the latest stored version are appended; for the others, every row.
    """
    model, _ = RAW_TABLES[table]
    if not rows:
        log.info("%s.%s: nothing to append", RAW_SCHEMA, table)
        return 0
    if any(not isinstance(row, model) for row in rows):
        raise TypeError(f"{table} takes {model.__name__} rows only")

    batch = pd.DataFrame([row.model_dump() for row in rows])
    batch["_loaded_at"] = loaded_at or datetime.now(UTC)
    batch["_source"] = source
    batch["_window_start"] = window_start
    batch["_window_end"] = window_end
    batch["_run_id"] = run_id

    columns = [*model.model_fields, *METADATA_FIELDS]
    # One statement, so a batch lands completely or not at all.
    con.register("_batch", batch)
    try:
        if table in POINT_KEYS:
            sql, params = _changed_rows_insert(table, columns, batch)
        else:
            listed = ", ".join(columns)
            sql, params = (
                f"insert into {RAW_SCHEMA}.{table} ({listed}) select {listed} from _batch",
                [],
            )
        (appended,) = con.execute(sql, params).fetchone()
    finally:
        con.unregister("_batch")
    log.info(
        "%s.%s: appended %d of %d rows (%d unchanged since the last load)",
        RAW_SCHEMA,
        table,
        appended,
        len(batch),
        len(batch) - appended,
    )
    return appended


def _changed_rows_insert(
    table: str, columns: list[str], batch: pd.DataFrame
) -> tuple[str, list[object]]:
    """An INSERT of the batch rows whose point is new, or whose values differ
    from the point's latest stored version.

    The latest versions are read only where the batch lies: its zones (the
    first key column) and its time span, bounds DuckDB can use to skip the
    row groups of other loads instead of reading the whole table.
    """
    keys, values = POINT_KEYS[table]
    scope = keys[0]
    zones = sorted(batch[scope].unique())
    first, last = batch["period_start_utc"].min(), batch["period_start_utc"].max()
    sql = f"""
        insert into {RAW_SCHEMA}.{table} ({", ".join(columns)})
        select {", ".join(f"b.{c}" for c in columns)}
        from _batch as b
        left join (
            select {", ".join([*keys, *values])}
            from {RAW_SCHEMA}.{table}
            where {scope} in ({", ".join("?" for _ in zones)})
                and period_start_utc between ? and ?
            qualify row_number() over (
                partition by {", ".join(keys)} order by _loaded_at desc
            ) = 1
        ) as stored
            on {" and ".join(f"b.{k} = stored.{k}" for k in keys)}
        where stored.{scope} is null
            or {" or ".join(f"b.{v} is distinct from stored.{v}" for v in values)}
    """  # noqa: S608 - identifiers come from POINT_KEYS, values are parameters
    return sql, [*zones, first.to_pydatetime(), last.to_pydatetime()]


if __name__ == "__main__":
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    with connect(settings) as connection:
        ensure_raw_tables(connection)
    log.info("Raw tables ready in %s", settings.database)
