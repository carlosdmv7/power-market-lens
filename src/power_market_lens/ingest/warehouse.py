"""Raw layer in MotherDuck: table definitions and append-only loads.

Raw is append-only (ADR-003): every load inserts the rows it fetched, stamped
with the same `_loaded_at`, its source and the window it asked for, and nothing
is ever updated or deleted. Re-pulling a trailing window therefore stores each
point once per pull, and staging keeps the latest version of each.

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
}

METADATA_FIELDS = ("_loaded_at", "_source", "_window_start", "_window_end", "_run_id")


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
    """Append one batch to a raw table in a single INSERT; return the row count."""
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

    columns = ", ".join([*model.model_fields, *METADATA_FIELDS])
    # One statement, so a batch lands completely or not at all.
    con.register("_batch", batch)
    try:
        con.execute(f"insert into {RAW_SCHEMA}.{table} ({columns}) select {columns} from _batch")
    finally:
        con.unregister("_batch")
    log.info("%s.%s: appended %d rows", RAW_SCHEMA, table, len(batch))
    return len(batch)


if __name__ == "__main__":
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    with connect(settings) as connection:
        ensure_raw_tables(connection)
    log.info("Raw tables ready in %s", settings.database)
