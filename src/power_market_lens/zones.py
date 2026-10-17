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
    # Some zones publish several day-ahead price series per day (e.g. DE-LU,
    # AT); position 1 is the SDAC result. None: the zone publishes only one.
    sdac_sequence: int | None = None


ZONES: dict[str, BiddingZone] = {
    "ES": BiddingZone(code="ES", eic="10YES-REE------0"),
}


def get_zone(code: str) -> BiddingZone:
    try:
        return ZONES[code]
    except KeyError:
        raise ValueError(f"Unknown bidding zone {code!r}; known: {sorted(ZONES)}") from None
