from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from power_market_lens.ingest.parsers import (
    EntsoeDocumentError,
    parse_crossborder_flows,
    parse_day_ahead_prices,
    parse_generation_per_type,
    parse_total_load,
)

FIXTURES = Path(__file__).parent / "fixtures" / "entsoe"
ES = "10YES-REE------0"
FR = "10YFR-RTE------C"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestDayAheadPrices:
    def test_a03_fills_omitted_positions_forward_across_the_25_hour_day(self):
        rows = parse_day_ahead_prices(fixture("synthetic_prices_a03_15min_dst_autumn.xml"))

        # 2025-10-26 in Madrid lasts 25 hours: 100 quarter-hours, although the
        # document only publishes 3 points.
        assert len(rows) == 100
        assert [r.price_eur_mwh for r in rows[:4]] == [50.0, 50.0, -1.5, -1.5]
        assert rows[98].price_eur_mwh == -1.5
        assert rows[99].price_eur_mwh == 12.0

    def test_intervals_are_contiguous_utc_quarter_hours(self):
        rows = parse_day_ahead_prices(fixture("synthetic_prices_a03_15min_dst_autumn.xml"))

        assert rows[0].period_start_utc == datetime(2025, 10, 25, 22, 0, tzinfo=UTC)
        assert rows[-1].period_start_utc == datetime(2025, 10, 26, 22, 45, tzinfo=UTC)
        steps = {b.period_start_utc - a.period_start_utc for a, b in pairwise(rows)}
        assert steps == {timedelta(minutes=15)}
        assert {r.resolution_minutes for r in rows} == {15}

    def test_carries_zone_units_and_document_metadata(self):
        row = parse_day_ahead_prices(fixture("synthetic_prices_a03_15min_dst_autumn.xml"))[0]

        assert row.bidding_zone_eic == ES
        assert (row.currency, row.price_unit) == ("EUR", "MWH")
        assert row.document_mrid == "SYNTH-A44-20251026"
        assert row.document_created_at == datetime(2025, 10, 25, 11, 5, tzinfo=UTC)

    def test_hourly_a01_on_the_23_hour_day(self):
        rows = parse_day_ahead_prices(fixture("synthetic_prices_a01_60min_dst_spring.xml"))

        assert len(rows) == 23
        assert {r.resolution_minutes for r in rows} == {60}
        assert rows[0].period_start_utc == datetime(2025, 3, 29, 23, 0, tzinfo=UTC)

    def test_out_of_range_prices_are_kept_not_dropped(self):
        # Flagging happens in dbt against the dated SDAC limits (ADR-003); the
        # parser must never filter a value it finds implausible.
        rows = parse_day_ahead_prices(fixture("synthetic_prices_a01_60min_dst_spring.xml"))

        assert max(r.price_eur_mwh for r in rows) == 4100.0


class TestGenerationPerType:
    def test_splits_generation_and_pumping_consumption(self):
        rows = parse_generation_per_type(fixture("synthetic_generation_per_type.xml"))

        solar = [r for r in rows if r.psr_type == "B16"]
        pumping = [r for r in rows if r.psr_type == "B10"]
        assert {r.flow_direction for r in solar} == {"generation"}
        assert {r.flow_direction for r in pumping} == {"consumption"}
        assert [r.quantity_mw for r in solar] == [18250, 18400, 18510, 18620]

    def test_a01_gap_stays_a_gap(self):
        # In A01 a missing position is missing data, not a repeated value;
        # inventing it would hide the gap from the completeness tests.
        rows = parse_generation_per_type(fixture("synthetic_generation_per_type.xml"))

        pumping = [r for r in rows if r.psr_type == "B10"]
        assert [r.period_start_utc.minute for r in pumping] == [0, 15, 45]


class TestRecordedResponses:
    """Real ENTSO-E responses, recorded with `make record-fixtures`."""

    def test_25_hour_day_expands_91_published_points_to_100_quarter_hours(self):
        rows = parse_day_ahead_prices(fixture("recorded_prices_a44_es_2025-10-26.xml"))

        assert len(rows) == 100
        assert rows[0].period_start_utc == datetime(2025, 10, 25, 22, 0, tzinfo=UTC)
        assert rows[-1].period_start_utc == datetime(2025, 10, 26, 22, 45, tzinfo=UTC)
        steps = {b.period_start_utc - a.period_start_utc for a, b in pairwise(rows)}
        assert steps == {timedelta(minutes=15)}
        # Position 20 is omitted, so it repeats position 19.
        assert rows[19].price_eur_mwh == rows[18].price_eur_mwh
        assert (rows[0].price_eur_mwh, rows[-1].price_eur_mwh) == (105.19, 88.1)

    def test_23_hour_day_keeps_negative_prices(self):
        rows = parse_day_ahead_prices(fixture("recorded_prices_a44_es_2026-03-29.xml"))

        assert len(rows) == 92
        assert {r.resolution_minutes for r in rows} == {15}
        assert min(r.price_eur_mwh for r in rows) == -10.0

    def test_generation_covers_every_series_and_pumping(self):
        rows = parse_generation_per_type(
            fixture("recorded_generation_a75_es_2026-10-17T10-16Z.xml")
        )

        # 23 series × 24 quarter-hours, although several series publish a
        # single A03 point for the whole window.
        assert len(rows) == 23 * 24
        series = {(r.psr_type, r.flow_direction) for r in rows}
        assert len(series) == 23
        assert {p for p, d in series if d == "consumption"} == {"B10", "B25"}
        pumping = [r for r in rows if (r.psr_type, r.flow_direction) == ("B10", "consumption")]
        assert pumping[0].quantity_mw == 3696.0
        fossil_gas = [r.quantity_mw for r in rows if r.psr_type == "B04"]
        assert fossil_gas[:2] == [2492.0, 2344.0]


class TestDayAheadAuctionSequences:
    def test_a_document_mixing_auctions_is_refused(self):
        # DE-LU, 2024: SDAC as sequence 1 (hourly) and a second auction as
        # sequence 2 (quarter-hourly). Kept together, the hourly mart's
        # finest-resolution rule would silently pick the wrong auction.
        with pytest.raises(EntsoeDocumentError, match=r"sequences \(1, 2\).*sdac_sequence"):
            parse_day_ahead_prices(
                fixture("recorded_prices_a44_de-lu_2024-03-15_all_sequences.xml")
            )

    def test_the_sdac_sequence_alone_parses(self):
        rows = parse_day_ahead_prices(
            fixture("recorded_prices_a44_de-lu_2024-03-15_sequence_1.xml")
        )

        assert len(rows) == 24
        assert {r.resolution_minutes for r in rows} == {60}
        assert rows[0].price_eur_mwh == 48.39


class TestTotalLoad:
    def test_actual_load_on_the_23_hour_day(self):
        rows = parse_total_load(fixture("recorded_load_a65_actual_es_2026-03-29.xml"))

        assert len(rows) == 92
        assert {(r.bidding_zone_eic, r.process_type, r.resolution_minutes) for r in rows} == {
            (ES, "A16", 15)
        }
        assert rows[0].period_start_utc == datetime(2026, 3, 28, 23, 0, tzinfo=UTC)
        assert [r.load_mw for r in rows[:3]] == [23872.0, 23288.0, 22912.0]

    def test_the_day_ahead_forecast_is_told_apart_by_process_type(self):
        rows = parse_total_load(fixture("recorded_load_a65_forecast_es_2026-03-29.xml"))

        assert len(rows) == 92
        assert {r.process_type for r in rows} == {"A01"}
        assert rows[0].load_mw == 24207.0

    def test_portugal_reports_load_hourly(self):
        rows = parse_total_load(fixture("recorded_load_a65_actual_pt_2026-03-29.xml"))

        assert len(rows) == 23
        assert {r.resolution_minutes for r in rows} == {60}

    def test_a_generation_document_is_not_load(self):
        with pytest.raises(EntsoeDocumentError, match="Expected document type A65, got A75"):
            parse_total_load(fixture("recorded_generation_a75_es_2026-10-17T10-16Z.xml"))


class TestCrossBorderFlows:
    def test_one_direction_of_a_border(self):
        rows = parse_crossborder_flows(fixture("recorded_flows_a11_es-fr_2026-03-29.xml"))

        assert len(rows) == 92
        assert {(r.out_zone_eic, r.in_zone_eic) for r in rows} == {(ES, FR)}
        assert [r.flow_mw for r in rows[:2]] == [3149.44, 3386.04]

    def test_a_direction_without_flow_is_one_a03_point_filled_to_zero(self):
        # Spain exported all day: France → Spain is published as a single
        # point, which must still expand to every quarter-hour.
        rows = parse_crossborder_flows(fixture("recorded_flows_a11_fr-es_2026-03-29.xml"))

        assert len(rows) == 92
        assert {r.flow_mw for r in rows} == {0.0}
        assert {(r.out_zone_eic, r.in_zone_eic) for r in rows} == {(FR, ES)}

    def test_a_price_document_is_not_a_flow(self):
        # Same root element (Publication_MarketDocument): only <type> differs.
        with pytest.raises(EntsoeDocumentError, match="Expected document type A11, got A44"):
            parse_crossborder_flows(fixture("recorded_prices_a44_es_2026-03-29.xml"))


class TestRejectsWhatItCannotTrust:
    def test_acknowledgement_raises_with_the_publisher_reason(self):
        with pytest.raises(EntsoeDocumentError, match="No matching data found"):
            parse_day_ahead_prices(fixture("synthetic_acknowledgement_no_data.xml"))

    def test_wrong_document_type(self):
        with pytest.raises(EntsoeDocumentError, match="Expected a Publication_MarketDocument"):
            parse_day_ahead_prices(fixture("synthetic_generation_per_type.xml"))

    def test_not_xml(self):
        with pytest.raises(EntsoeDocumentError, match="not valid XML"):
            parse_day_ahead_prices("<html>Service unavailable")

    def test_unsupported_resolution(self):
        xml = fixture("synthetic_prices_a01_60min_dst_spring.xml").replace("PT60M", "P1D")
        with pytest.raises(EntsoeDocumentError, match="Unsupported resolution"):
            parse_day_ahead_prices(xml)

    def test_position_outside_the_period(self):
        xml = fixture("synthetic_prices_a01_60min_dst_spring.xml").replace(
            "<position>23</position>", "<position>24</position>"
        )
        with pytest.raises(EntsoeDocumentError, match="outside the period"):
            parse_day_ahead_prices(xml)

    def test_a03_without_a_first_point(self):
        xml = fixture("synthetic_prices_a03_15min_dst_autumn.xml").replace(
            "<position>1</position>", "<position>2</position>"
        )
        with pytest.raises(EntsoeDocumentError, match="position 1"):
            parse_day_ahead_prices(xml)
