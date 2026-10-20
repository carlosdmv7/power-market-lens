"""Re-record the real ENTSO-E responses the parser tests run against.

    make record-fixtures                                  # all of them
    uv run --env-file .env python scripts/record_entsoe_fixtures.py --only load

Needs ENTSOE_API_KEY. The responses carry no credentials (the token travels in
the request URL, never in the document), but read the diff before committing.
The windows are fixed, but a re-recording still differs from the last one even
when no data was revised: ENTSO-E builds the document per request, with fresh
mRIDs and createdDateTime and the time series in a different order. Record only
what a test needs (`--only`), and commit a re-recording only for new content.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

from entsoe import EntsoeRawClient
import pandas as pd

from power_market_lens.config import load_settings
from power_market_lens.zones import get_zone

FIXTURES = Path(__file__).parents[1] / "tests" / "ingest" / "fixtures" / "entsoe"


@dataclass(frozen=True)
class Recording:
    document: str  # A44 prices, A75 generation, A65 load, A11 flows
    zone: str  # for A11, the zone the flow leaves
    start: str  # UTC
    end: str
    in_zone: str | None = None  # A11 only: the zone the flow enters
    sequence: int | None = None  # A44 only: the auction (classification sequence)
    forecast: bool = False  # A65 only: day-ahead forecast instead of actual load


RECORDINGS = {
    # Autumn DST change: the Madrid delivery day lasts 25 hours.
    "recorded_prices_a44_es_2025-10-26.xml": Recording(
        "A44", "ES", "2025-10-25T22:00Z", "2025-10-26T23:00Z"
    ),
    # Spring DST change: 23 hours, with negative prices.
    "recorded_prices_a44_es_2026-03-29.xml": Recording(
        "A44", "ES", "2026-03-28T23:00Z", "2026-03-29T22:00Z"
    ),
    # DE-LU on a 2024 day, no sequence requested: SDAC (sequence 1, hourly)
    # and a second auction (sequence 2, quarter-hourly) in one document.
    "recorded_prices_a44_de-lu_2024-03-15_all_sequences.xml": Recording(
        "A44", "DE-LU", "2024-03-14T23:00Z", "2024-03-15T23:00Z"
    ),
    # The same day with sequence 1 requested: SDAC only.
    "recorded_prices_a44_de-lu_2024-03-15_sequence_1.xml": Recording(
        "A44", "DE-LU", "2024-03-14T23:00Z", "2024-03-15T23:00Z", sequence=1
    ),
    # Six hours of every production type, pumping included.
    "recorded_generation_a75_es_2026-10-17T10-16Z.xml": Recording(
        "A75", "ES", "2026-10-17T10:00Z", "2026-10-17T16:00Z"
    ),
    # Load on the 23-hour day: actual and the day-ahead forecast, and Portugal,
    # which reports hourly.
    "recorded_load_a65_actual_es_2026-03-29.xml": Recording(
        "A65", "ES", "2026-03-28T23:00Z", "2026-03-29T22:00Z"
    ),
    "recorded_load_a65_forecast_es_2026-03-29.xml": Recording(
        "A65", "ES", "2026-03-28T23:00Z", "2026-03-29T22:00Z", forecast=True
    ),
    "recorded_load_a65_actual_pt_2026-03-29.xml": Recording(
        "A65", "PT", "2026-03-28T23:00Z", "2026-03-29T22:00Z"
    ),
    # Both directions of one border on the same day. Spain exported all day,
    # so France → Spain is a single A03 point: zero for every quarter-hour.
    "recorded_flows_a11_es-fr_2026-03-29.xml": Recording(
        "A11", "ES", "2026-03-28T23:00Z", "2026-03-29T22:00Z", in_zone="FR"
    ),
    "recorded_flows_a11_fr-es_2026-03-29.xml": Recording(
        "A11", "FR", "2026-03-28T23:00Z", "2026-03-29T22:00Z", in_zone="ES"
    ),
}


def fetch(client: EntsoeRawClient, recording: Recording) -> str:
    eic = get_zone(recording.zone).eic
    bounds = {"start": pd.Timestamp(recording.start), "end": pd.Timestamp(recording.end)}
    match recording.document:
        case "A44":
            return client.query_day_ahead_prices(eic, **bounds, sequence=recording.sequence)
        case "A75":
            return client.query_generation(eic, **bounds)
        case "A65" if recording.forecast:
            return client.query_load_forecast(eic, **bounds, process_type="A01")
        case "A65":
            return client.query_load(eic, **bounds)
        case "A11":
            in_eic = get_zone(recording.in_zone).eic
            return client.query_crossborder_flows(eic, in_eic, **bounds)
    raise ValueError(f"Unknown document {recording.document}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="record only names containing this text")
    args = parser.parse_args()

    settings = load_settings()
    if settings.entsoe_api_key is None:
        raise SystemExit("ENTSOE_API_KEY is not set; see .env.example")
    client = EntsoeRawClient(api_key=settings.entsoe_api_key.get_secret_value(), timeout=90)
    for name, recording in RECORDINGS.items():
        if args.only in name:
            xml_text = fetch(client, recording)
            (FIXTURES / name).write_text(xml_text)
            print(f"{name}: {len(xml_text):,} bytes")


if __name__ == "__main__":
    main()
