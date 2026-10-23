"""Ingestion jobs: fetch one entity for one zone and window, append it to raw.

These are plain functions so they can be tested without Prefect; the flows
wrap them as tasks to add retries, logging and run history (ADR-002).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import duckdb

from power_market_lens.ingest.entsoe_client import EntsoeClient
from power_market_lens.ingest.warehouse import append_rows
from power_market_lens.zones import BiddingZone, directions_owned_by

# Concurrent A11 requests per zone. Each takes ~11 s of ENTSO-E server time, so
# sequential requests made the flows a third of a daily run (66 of them, 12
# min). Six in flight is ~33 requests a minute, far below the 400 a minute the
# Transparency Platform allows per token.
FLOW_WORKERS = 6


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


def load_total_load_actual(
    client: EntsoeClient,
    con: duckdb.DuckDBPyConnection,
    zone: BiddingZone,
    start: datetime,
    end: datetime,
    *,
    run_id: str,
) -> int:
    rows = client.total_load(zone, start, end, forecast=False)
    return append_rows(
        con,
        "entsoe_total_load",
        rows,
        source="entsoe:A65/A16",
        window_start=start,
        window_end=end,
        run_id=run_id,
    )


def load_total_load_forecast(
    client: EntsoeClient,
    con: duckdb.DuckDBPyConnection,
    zone: BiddingZone,
    start: datetime,
    end: datetime,
    *,
    run_id: str,
) -> int:
    rows = client.total_load(zone, start, end, forecast=True)
    return append_rows(
        con,
        "entsoe_total_load",
        rows,
        source="entsoe:A65/A01",
        window_start=start,
        window_end=end,
        run_id=run_id,
    )


def load_crossborder_flows(
    client: EntsoeClient,
    con: duckdb.DuckDBPyConnection,
    zone: BiddingZone,
    start: datetime,
    end: datetime,
    *,
    run_id: str,
) -> int:
    """Physical flows on every border `zone` owns (zones.BORDERS), both ways.

    A zone that owns no border loads nothing: its borders are another zone's.
    The directions are fetched concurrently and written in one append, so a
    failed request still leaves raw untouched for the task retry.
    """
    with ThreadPoolExecutor(max_workers=FLOW_WORKERS) as pool:
        per_direction = pool.map(
            lambda pair: client.crossborder_flows(*pair, start, end),
            directions_owned_by(zone.code),
        )
        rows = [row for direction_rows in per_direction for row in direction_rows]
    return append_rows(
        con,
        "entsoe_crossborder_flows",
        rows,
        source="entsoe:A11",
        window_start=start,
        window_end=end,
        run_id=run_id,
    )
