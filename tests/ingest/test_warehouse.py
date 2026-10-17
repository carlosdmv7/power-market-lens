from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from power_market_lens.config import Settings
from power_market_lens.ingest.entsoe_client import EntsoeClient
from power_market_lens.ingest.jobs import load_day_ahead_prices, load_generation_per_type
from power_market_lens.ingest.parsers import parse_generation_per_type
from power_market_lens.ingest.warehouse import (
    METADATA_FIELDS,
    RAW_TABLES,
    append_rows,
    connect,
    ensure_raw_tables,
)
from power_market_lens.zones import get_zone

FIXTURES = Path(__file__).parent / "fixtures" / "entsoe"
WINDOW = {
    "source": "test",
    "window_start": datetime(2026, 7, 1, tzinfo=UTC),
    "window_end": datetime(2026, 7, 2, tzinfo=UTC),
    "run_id": "test-run",
}


@pytest.fixture
def con():
    # An in-memory DuckDB: same SQL as MotherDuck, no network (CLAUDE.md).
    connection = duckdb.connect()
    ensure_raw_tables(connection)
    yield connection
    connection.close()


class _ReplayRawClient:
    def __init__(self, xml):
        self.xml = xml

    def query_day_ahead_prices(self, *_, **__):
        return self.xml

    def query_generation(self, *_, **__):
        return self.xml


def client_replaying(fixture: str) -> EntsoeClient:
    return EntsoeClient(_ReplayRawClient((FIXTURES / fixture).read_text()), sleep=lambda _: None)


def test_table_columns_mirror_their_row_model(con):
    for table, (model, _) in RAW_TABLES.items():
        columns = [
            name
            for (name,) in con.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'raw' and table_name = ? order by ordinal_position",
                [table],
            ).fetchall()
        ]
        assert columns == [*model.model_fields, *METADATA_FIELDS], table


def test_ensure_raw_tables_is_idempotent(con):
    ensure_raw_tables(con)  # second call on purpose: must not fail or recreate


def test_append_stamps_every_row_with_the_batch_metadata(con):
    rows = parse_generation_per_type((FIXTURES / "synthetic_generation_per_type.xml").read_text())
    loaded_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    count = append_rows(con, "entsoe_generation_per_type", rows, loaded_at=loaded_at, **WINDOW)

    assert count == len(rows) == 7
    stamps = con.execute(
        "select distinct _loaded_at, _source, _window_start, _window_end, _run_id "
        "from raw.entsoe_generation_per_type"
    ).fetchall()
    assert stamps == [(loaded_at, "test", WINDOW["window_start"], WINDOW["window_end"], "test-run")]


def test_timestamps_survive_the_round_trip_as_utc_instants(con):
    rows = parse_generation_per_type((FIXTURES / "synthetic_generation_per_type.xml").read_text())
    append_rows(con, "entsoe_generation_per_type", rows, **WINDOW)

    first = con.execute(
        "select min(period_start_utc) from raw.entsoe_generation_per_type"
    ).fetchone()[0]
    assert first == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def test_empty_batch_inserts_nothing(con):
    assert append_rows(con, "entsoe_day_ahead_prices", [], **WINDOW) == 0


def test_rejects_rows_of_the_wrong_entity(con):
    rows = parse_generation_per_type((FIXTURES / "synthetic_generation_per_type.xml").read_text())

    with pytest.raises(TypeError, match="DayAheadPriceRow"):
        append_rows(con, "entsoe_day_ahead_prices", rows, **WINDOW)


def test_reloading_a_window_appends_never_updates(con):
    # Append-only (ADR-003): a re-pull adds a second copy; staging dedups it.
    client = client_replaying("synthetic_prices_a03_15min_dst_autumn.xml")
    zone = get_zone("ES")
    start, end = datetime(2025, 10, 25, 22, tzinfo=UTC), datetime(2025, 10, 26, 23, tzinfo=UTC)

    load_day_ahead_prices(client, con, zone, start, end, run_id="first")
    load_day_ahead_prices(client, con, zone, start, end, run_id="second")

    total, distinct_points, runs = con.execute(
        "select count(*), count(distinct (period_start_utc, resolution_minutes)), "
        "count(distinct _run_id) from raw.entsoe_day_ahead_prices"
    ).fetchone()
    assert (total, distinct_points, runs) == (200, 100, 2)


def test_generation_job_tags_its_source(con):
    client = client_replaying("synthetic_generation_per_type.xml")

    load_generation_per_type(
        client,
        con,
        get_zone("ES"),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
        run_id="r",
    )

    assert con.execute(
        "select distinct _source from raw.entsoe_generation_per_type"
    ).fetchall() == [("entsoe:A75",)]


def test_connect_requires_a_token():
    with pytest.raises(RuntimeError, match="MOTHERDUCK_TOKEN"):
        connect(Settings(_env_file=None, motherduck_token=None))
