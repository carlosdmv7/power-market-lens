"""Ingestion jobs: fetch one entity for one zone and window, append it to raw.

These are plain functions so they can be tested without Prefect; the flows
wrap them as tasks to add retries, logging and run history (ADR-002).
"""

from datetime import datetime

import duckdb

from power_market_lens.ingest.entsoe_client import EntsoeClient
from power_market_lens.ingest.warehouse import append_rows
from power_market_lens.zones import BiddingZone


def load_day_ahead_prices(
    client: EntsoeClient,
    con: duckdb.DuckDBPyConnection,
    zone: BiddingZone,
    start: datetime,
    end: datetime,
    *,
    run_id: str,
) -> int:
    rows = client.day_ahead_prices(zone, start, end)
    return append_rows(
        con,
        "entsoe_day_ahead_prices",
        rows,
        source="entsoe:A44",
        window_start=start,
        window_end=end,
        run_id=run_id,
    )


def load_generation_per_type(
    client: EntsoeClient,
    con: duckdb.DuckDBPyConnection,
    zone: BiddingZone,
    start: datetime,
    end: datetime,
    *,
    run_id: str,
) -> int:
    rows = client.generation_per_type(zone, start, end)
    return append_rows(
        con,
        "entsoe_generation_per_type",
        rows,
        source="entsoe:A75",
        window_start=start,
        window_end=end,
        run_id=run_id,
    )
