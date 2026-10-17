import csv
from pathlib import Path

import pytest

from power_market_lens.zones import ZONES, get_zone

SEED = Path(__file__).parents[1] / "transform" / "seeds" / "bidding_zones.csv"


def test_every_ingested_zone_is_in_the_dbt_seed_with_the_same_eic():
    # The seed is the warehouse's reference table; a zone ingested but missing
    # from it would get no time zone, so no local dates, in the marts.
    with SEED.open() as f:
        seed = {row["zone_code"]: row["bidding_zone_eic"] for row in csv.DictReader(f)}

    assert {code: zone.eic for code, zone in ZONES.items()} == seed


def test_unknown_zone_names_the_known_ones():
    with pytest.raises(ValueError, match=r"Unknown bidding zone 'XX'.*\['ES'\]"):
        get_zone("XX")
