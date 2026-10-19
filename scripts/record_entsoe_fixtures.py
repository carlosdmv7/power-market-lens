"""Re-record the real ENTSO-E responses the parser tests run against.

    make record-fixtures

Needs ENTSOE_API_KEY. The responses carry no credentials (the token travels in
the request URL, never in the document), but read the diff before committing.
The windows are fixed, but a re-recording still differs from the last one even
when no data was revised: ENTSO-E builds the document per request, with fresh
mRIDs and createdDateTime and the time series in a different order. Commit a
re-recording only when a test needs new content.
"""

from pathlib import Path

from entsoe import EntsoeRawClient
import pandas as pd

from power_market_lens.config import load_settings
from power_market_lens.zones import get_zone

FIXTURES = Path(__file__).parents[1] / "tests" / "ingest" / "fixtures" / "entsoe"
DE_LU_EIC = "10Y1001A1001A82H"

# name -> (document, start, end in UTC[, classification sequence])
RECORDINGS = {
    # Autumn DST change: the Madrid delivery day lasts 25 hours.
    "recorded_prices_a44_es_2025-10-26.xml": ("A44", "2025-10-25T22:00Z", "2025-10-26T23:00Z"),
    # Spring DST change: 23 hours, with negative prices.
    "recorded_prices_a44_es_2026-03-29.xml": ("A44", "2026-03-28T23:00Z", "2026-03-29T22:00Z"),
    # DE-LU on a 2024 day, no sequence requested: SDAC (sequence 1, hourly)
    # and a second auction (sequence 2, quarter-hourly) in one document.
    "recorded_prices_a44_de-lu_2024-03-15_all_sequences.xml": (
        "A44",
        "2024-03-14T23:00Z",
        "2024-03-15T23:00Z",
    ),
    # The same day with sequence 1 requested: SDAC only.
    "recorded_prices_a44_de-lu_2024-03-15_sequence_1.xml": (
        "A44",
        "2024-03-14T23:00Z",
        "2024-03-15T23:00Z",
        1,
    ),
    # Six hours of every production type, pumping included.
    "recorded_generation_a75_es_2026-10-17T10-16Z.xml": (
        "A75",
        "2026-10-17T10:00Z",
        "2026-10-17T16:00Z",
    ),
}


def main() -> None:
    settings = load_settings()
    if settings.entsoe_api_key is None:
        raise SystemExit("ENTSOE_API_KEY is not set; see .env.example")
    client = EntsoeRawClient(api_key=settings.entsoe_api_key.get_secret_value(), timeout=60)
    for name, (document, start, end, *sequence) in RECORDINGS.items():
        # The zone is in the file name; DE-LU is not one of ZONES yet.
        eic = DE_LU_EIC if "_de-lu_" in name else get_zone("ES").eic
        bounds = {"start": pd.Timestamp(start), "end": pd.Timestamp(end)}
        if document == "A44":
            xml_text = client.query_day_ahead_prices(
                eic, **bounds, sequence=next(iter(sequence), None)
            )
        else:
            xml_text = client.query_generation(eic, **bounds)
        (FIXTURES / name).write_text(xml_text)
        print(f"{name}: {len(xml_text):,} bytes")


if __name__ == "__main__":
    main()
