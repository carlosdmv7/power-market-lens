from datetime import UTC, datetime
from pathlib import Path
import re
import threading

import duckdb
import pytest

from power_market_lens.config import Settings
from power_market_lens.ingest.entsoe_client import EntsoeClient
from power_market_lens.ingest.jobs import (
    load_crossborder_flows,
    load_day_ahead_prices,
    load_generation_per_type,
)
from power_market_lens.ingest.models import DayAheadPriceRow
from power_market_lens.ingest.parsers import parse_generation_per_type
from power_market_lens.ingest.warehouse import (
    METADATA_FIELDS,
    POINT_KEYS,
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


def test_reloading_an_unchanged_window_appends_nothing(con):
    # ADR-011: a re-pull that brings no revision stores no second copy.
    client = client_replaying("synthetic_prices_a03_15min_dst_autumn.xml")
    zone = get_zone("ES")
    start, end = datetime(2025, 10, 25, 22, tzinfo=UTC), datetime(2025, 10, 26, 23, tzinfo=UTC)

    first = load_day_ahead_prices(client, con, zone, start, end, run_id="first")
    second = load_day_ahead_prices(client, con, zone, start, end, run_id="second")

    total, distinct_points, runs = con.execute(
        "select count(*), count(distinct (period_start_utc, resolution_minutes)), "
        "count(distinct _run_id) from raw.entsoe_day_ahead_prices"
    ).fetchone()
    assert (first, second) == (100, 0)
    assert (total, distinct_points, runs) == (100, 100, 1)


def _price(hour: int, value: float, *, request: str = "a", zone: str = "ES") -> DayAheadPriceRow:
    """A price point as a request returns it; `request` stands for the id and
    creation time ENTSO-E stamps on every response."""
    return DayAheadPriceRow(
        bidding_zone_eic=get_zone(zone).eic,
        period_start_utc=datetime(2026, 7, 1, hour, tzinfo=UTC),
        resolution_minutes=15,
        price_eur_mwh=value,
        currency="EUR",
        price_unit="MWH",
        document_mrid=f"mrid-{request}",
        document_created_at=datetime(2026, 7, 2, ord(request) % 24, tzinfo=UTC),
    )


def _load(con, rows, run: str) -> int:
    return append_rows(
        con,
        "entsoe_day_ahead_prices",
        rows,
        loaded_at=datetime(2026, 7, 2, 1 + ord(run) % 20, tzinfo=UTC),
        **{**WINDOW, "run_id": run},
    )


def _stored(con) -> list[tuple]:
    return con.execute(
        "select hour(period_start_utc at time zone 'UTC'), price_eur_mwh, _run_id "
        "from raw.entsoe_day_ahead_prices order by _loaded_at, period_start_utc"
    ).fetchall()


def test_a_new_request_id_alone_is_not_a_revision(con):
    _load(con, [_price(10, 50.0, request="a")], run="a")

    assert _load(con, [_price(10, 50.0, request="b")], run="b") == 0
    assert _stored(con) == [(10, 50.0, "a")]


def test_only_the_revised_and_the_new_points_are_appended(con):
    _load(con, [_price(10, 50.0), _price(11, 60.0)], run="a")

    # 10:00 unchanged, 11:00 revised, 12:00 published late.
    appended = _load(con, [_price(10, 50.0), _price(11, 61.5), _price(12, 70.0)], run="b")

    assert appended == 2
    assert _stored(con) == [(10, 50.0, "a"), (11, 60.0, "a"), (11, 61.5, "b"), (12, 70.0, "b")]


def test_a_value_that_returns_is_compared_with_the_latest_version_only(con):
    # 60 → 61.5 → 60: the third load differs from the latest version, so it is
    # stored, and staging's latest version is 60 again.
    _load(con, [_price(11, 60.0)], run="a")
    _load(con, [_price(11, 61.5)], run="b")

    assert _load(con, [_price(11, 60.0)], run="c") == 1
    assert [row[2] for row in _stored(con)] == ["a", "b", "c"]


def test_the_same_point_of_another_zone_is_a_different_point(con):
    _load(con, [_price(10, 50.0, zone="ES")], run="a")

    assert _load(con, [_price(10, 50.0, zone="PT")], run="b") == 1


def test_point_keys_are_the_columns_staging_deduplicates_on():
    # If they drifted apart, a load could skip a row that staging would treat
    # as a different point, or store copies staging folds together.
    staging = Path(__file__).parents[2] / "transform" / "models" / "staging" / "entsoe"
    for table, (keys, values) in POINT_KEYS.items():
        sql = (staging / f"stg_{table.replace('entsoe_', 'entsoe__')}.sql").read_text()
        partition = re.search(r"partition by\s+(.+?)\s+order by", sql, re.DOTALL)
        assert partition, table
        assert [c.strip() for c in partition.group(1).split(",")] == list(keys), table
        model, _ = RAW_TABLES[table]
        compared = set(keys) | set(values) | {"document_mrid", "document_created_at"}
        assert compared == set(model.model_fields), table  # every other column is compared


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


class _FlowsByDirection:
    """Replays the recorded ES → FR and FR → ES documents; records each request."""

    def __init__(self):
        self.requests = []

    def query_crossborder_flows(self, country_code_from, country_code_to, **_):
        self.requests.append((country_code_from, country_code_to))
        direction = "es-fr" if country_code_from == get_zone("ES").eic else "fr-es"
        return (FIXTURES / f"recorded_flows_a11_{direction}_2026-03-29.xml").read_text()


def test_flows_job_loads_both_directions_of_each_border_the_zone_owns(con, monkeypatch):
    raw = _FlowsByDirection()
    monkeypatch.setattr(
        "power_market_lens.ingest.jobs.directions_owned_by",
        lambda code: [(get_zone("ES"), get_zone("FR")), (get_zone("FR"), get_zone("ES"))],
    )
    start, end = datetime(2026, 3, 28, 23, tzinfo=UTC), datetime(2026, 3, 29, 22, tzinfo=UTC)

    count = load_crossborder_flows(
        EntsoeClient(raw, sleep=lambda _: None), con, get_zone("ES"), start, end, run_id="r"
    )

    es, fr = get_zone("ES").eic, get_zone("FR").eic
    assert sorted(raw.requests) == sorted([(es, fr), (fr, es)])  # fetched concurrently
    assert count == 2 * 92
    assert con.execute(
        "select _source, out_zone_eic, in_zone_eic, count(*) from raw.entsoe_crossborder_flows "
        "group by all order by all"
    ).fetchall() == [("entsoe:A11", es, fr, 92), ("entsoe:A11", fr, es, 92)]


def test_flows_job_fetches_the_directions_concurrently(con, monkeypatch):
    raw = _FlowsByDirection()
    # Each request waits for the other one: done one at a time, the first times out.
    both_in_flight = threading.Barrier(2, timeout=5)
    replay = raw.query_crossborder_flows

    def query(*args, **kwargs):
        both_in_flight.wait()
        return replay(*args, **kwargs)

    raw.query_crossborder_flows = query
    monkeypatch.setattr(
        "power_market_lens.ingest.jobs.directions_owned_by",
        lambda code: [(get_zone("ES"), get_zone("FR")), (get_zone("FR"), get_zone("ES"))],
    )
    start, end = datetime(2026, 3, 28, 23, tzinfo=UTC), datetime(2026, 3, 29, 22, tzinfo=UTC)

    count = load_crossborder_flows(
        EntsoeClient(raw, sleep=lambda _: None), con, get_zone("ES"), start, end, run_id="r"
    )

    assert count == 2 * 92


def test_a_zone_without_borders_of_its_own_requests_nothing(con):
    raw = _FlowsByDirection()
    start, end = datetime(2026, 3, 28, 23, tzinfo=UTC), datetime(2026, 3, 29, 22, tzinfo=UTC)

    count = load_crossborder_flows(
        EntsoeClient(raw, sleep=lambda _: None), con, get_zone("IT-SARD"), start, end, run_id="r"
    )

    assert (count, raw.requests) == (0, [])


def test_connect_requires_a_token():
    with pytest.raises(RuntimeError, match="MOTHERDUCK_TOKEN"):
        connect(Settings(_env_file=None, motherduck_token=None))
