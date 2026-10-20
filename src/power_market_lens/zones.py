"""Bidding zones the pipeline ingests, and the borders it loads flows on.

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


# Zones outside the twelve that border one of them. Only their physical flows
# with a modelled zone are loaded, never their prices or generation.
NEIGHBOURS: dict[str, BiddingZone] = {
    zone.code: zone
    for zone in [
        BiddingZone(code="AT", eic="10YAT-APG------L"),
        BiddingZone(code="BE", eic="10YBE----------2"),
        BiddingZone(code="CH", eic="10YCH-SWISSGRIDZ"),
        BiddingZone(code="CZ", eic="10YCZ-CEPS-----N"),
        BiddingZone(code="DK-1", eic="10YDK-1--------W"),
        BiddingZone(code="DK-2", eic="10YDK-2--------M"),
        BiddingZone(code="GB", eic="10YGB----------A"),
        BiddingZone(code="GR", eic="10YGR-HTSO-----Y"),
        BiddingZone(code="ME", eic="10YCS-CG-TSO---S"),
        BiddingZone(code="MT", eic="10Y1001A1001A93C"),
        BiddingZone(code="NO-2", eic="10YNO-2--------T"),
        BiddingZone(code="PL", eic="10YPL-AREA-----S"),
        BiddingZone(code="SE-4", eic="10Y1001A1001A47J"),
        BiddingZone(code="SI", eic="10YSI-ELES-----O"),
    ]
}

# Every border of a modelled zone with physical flows (A11) published in both
# directions, found by querying each modelled zone against every other and
# against seventeen candidate neighbours for 2026-10-19. The first zone of a
# pair owns the border: its flows job loads both directions, so no border is
# fetched twice. Owners are the earlier zone in ZONES order; a test keeps it so.
BORDERS: tuple[tuple[str, str], ...] = (
    ("ES", "PT"),
    ("ES", "FR"),
    ("FR", "DE-LU"),
    ("FR", "IT-NORD"),
    ("FR", "BE"),
    ("FR", "CH"),
    ("FR", "GB"),
    ("DE-LU", "NL"),
    ("DE-LU", "AT"),
    ("DE-LU", "BE"),
    ("DE-LU", "CH"),
    ("DE-LU", "CZ"),
    ("DE-LU", "DK-1"),
    ("DE-LU", "DK-2"),
    ("DE-LU", "NO-2"),
    ("DE-LU", "PL"),
    ("DE-LU", "SE-4"),
    ("NL", "BE"),
    ("NL", "DK-1"),
    ("NL", "GB"),
    ("NL", "NO-2"),
    ("IT-NORD", "IT-CNOR"),
    ("IT-NORD", "AT"),
    ("IT-NORD", "CH"),
    ("IT-NORD", "SI"),
    ("IT-CNOR", "IT-CSUD"),
    ("IT-CSUD", "IT-SUD"),
    ("IT-CSUD", "IT-SARD"),
    ("IT-CSUD", "ME"),
    ("IT-SUD", "IT-CALA"),
    ("IT-SUD", "GR"),
    ("IT-CALA", "IT-SICI"),
    ("IT-SICI", "MT"),
)


def get_area(code: str) -> BiddingZone:
    """A modelled zone or a neighbour."""
    return NEIGHBOURS[code] if code in NEIGHBOURS else get_zone(code)


def directions_owned_by(code: str) -> list[tuple[BiddingZone, BiddingZone]]:
    """(out, in) pairs of every border `code` owns, both directions of each."""
    return [
        pair
        for owner, other in BORDERS
        if owner == code
        for pair in ((get_area(owner), get_area(other)), (get_area(other), get_area(owner)))
    ]


def get_zone(code: str) -> BiddingZone:
    try:
        return ZONES[code]
    except KeyError:
        raise ValueError(f"Unknown bidding zone {code!r}; known: {sorted(ZONES)}") from None
