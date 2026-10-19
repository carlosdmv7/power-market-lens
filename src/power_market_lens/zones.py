"""Bidding zones the pipeline ingests.

The dbt seed `transform/seeds/bidding_zones.csv` is the reference table for the
warehouse (country, time zone). This module holds only what the ingestion needs
to build API requests; a test keeps the two in sync.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BiddingZone:
    code: str
    eic: str
    # Some zones publish several day-ahead auctions as classification
    # sequences (DE-LU). None: the zone publishes one, unlabelled; asking such
    # a zone for a sequence returns no data.
    sdac_sequence: int | None = None


ZONES: dict[str, BiddingZone] = {
    zone.code: zone
    for zone in [
        BiddingZone(code="ES", eic="10YES-REE------0"),
        BiddingZone(code="PT", eic="10YPT-REN------W"),
        BiddingZone(code="FR", eic="10YFR-RTE------C"),
        # Sequence 1 is SDAC, checked against Bundesnetzagentur's day-ahead
        # price; sequence 2 is a second auction at other prices (and, before
        # October 2025, another resolution). The parser refuses a mix.
        BiddingZone(code="DE-LU", eic="10Y1001A1001A82H", sdac_sequence=1),
        BiddingZone(code="NL", eic="10YNL----------L"),
        BiddingZone(code="IT-NORD", eic="10Y1001A1001A73I"),
        BiddingZone(code="IT-CNOR", eic="10Y1001A1001A70O"),
        BiddingZone(code="IT-CSUD", eic="10Y1001A1001A71M"),
        BiddingZone(code="IT-SUD", eic="10Y1001A1001A788"),
        BiddingZone(code="IT-CALA", eic="10Y1001C--00096J"),
        BiddingZone(code="IT-SICI", eic="10Y1001A1001A75E"),
        BiddingZone(code="IT-SARD", eic="10Y1001A1001A74G"),
    ]
}


def get_zone(code: str) -> BiddingZone:
    try:
        return ZONES[code]
    except KeyError:
        raise ValueError(f"Unknown bidding zone {code!r}; known: {sorted(ZONES)}") from None
