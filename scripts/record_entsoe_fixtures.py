"""Re-record the real ENTSO-E responses the parser tests run against.

    make record-fixtures

Needs ENTSOE_API_KEY. The responses carry no credentials (the token travels in
the request URL, never in the document), but read the diff before committing.
The windows are fixed so a re-recording only changes what ENTSO-E revised.
"""

from pathlib import Path

from entsoe import EntsoeRawClient
import pandas as pd

from power_market_lens.config import load_settings
from power_market_lens.zones import get_zone

FIXTURES = Path(__file__).parents[1] / "tests" / "ingest" / "fixtures" / "entsoe"

# name -> (document, start, end) in UTC
RECORDINGS = {
    # Autumn DST change: the Madrid delivery day lasts 25 hours.
    "recorded_prices_a44_es_2025-10-26.xml": ("A44", "2025-10-25T22:00Z", "2025-10-26T23:00Z"),
    # Spring DST change: 23 hours, with negative prices.
    "recorded_prices_a44_es_2026-03-29.xml": ("A44", "2026-03-28T23:00Z", "2026-03-29T22:00Z"),
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
    zone = get_zone("ES")
    for name, (document, start, end) in RECORDINGS.items():
        bounds = {"start": pd.Timestamp(start), "end": pd.Timestamp(end)}
        if document == "A44":
            xml_text = client.query_day_ahead_prices(zone.eic, **bounds)
        else:
            xml_text = client.query_generation(zone.eic, **bounds)
        (FIXTURES / name).write_text(xml_text)
        print(f"{name}: {len(xml_text):,} bytes")


if __name__ == "__main__":
    main()
