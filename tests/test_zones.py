import csv
from pathlib import Path
from zoneinfo import ZoneInfo

from entsoe.mappings import Area
import pytest

from power_market_lens.zones import BORDERS, NEIGHBOURS, ZONES, directions_owned_by, get_zone

SEED = Path(__file__).parents[1] / "transform" / "seeds" / "bidding_zones.csv"


def test_every_ingested_zone_is_in_the_dbt_seed_with_the_same_eic():
    # The seed is the warehouse's reference table; a zone ingested but missing
    # from it would get no time zone, so no local dates, in the marts.
    with SEED.open() as f:
        seed = {row["zone_code"]: row["bidding_zone_eic"] for row in csv.DictReader(f)}

    assert {code: zone.eic for code, zone in ZONES.items()} == seed


def test_the_seed_maps_every_zone_to_a_real_time_zone():
    with SEED.open() as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        ZoneInfo(row["timezone"])  # raises on a typo
    assert len({row["zone_name"] for row in rows}) == len(rows)


def test_only_de_lu_selects_an_auction_sequence():
    # Asking an unlabelled zone for a sequence returns no data at all.
    assert {code for code, zone in ZONES.items() if zone.sdac_sequence} == {"DE-LU"}


def test_unknown_zone_names_the_known_ones():
    with pytest.raises(ValueError, match=r"Unknown bidding zone 'XX'.*'ES'"):
        get_zone("XX")


def test_every_eic_matches_the_entsoe_area_of_the_same_name():
    # A typo in a hand-copied EIC would only show as "no data" at run time.
    for code, zone in {**ZONES, **NEIGHBOURS}.items():
        assert Area[code.replace("-", "_")].value == zone.eic, code


def test_each_border_is_listed_once_and_owned_by_a_modelled_zone():
    order = list(ZONES)
    assert len({frozenset(border) for border in BORDERS}) == len(BORDERS)
    for owner, other in BORDERS:
        assert owner in ZONES
        assert other in ZONES or other in NEIGHBOURS
        if other in ZONES:  # the earlier zone owns a border between two modelled ones
            assert order.index(owner) < order.index(other), (owner, other)


def test_every_neighbour_is_on_a_border():
    assert {other for _, other in BORDERS if other in NEIGHBOURS} == set(NEIGHBOURS)


def test_an_owner_loads_both_directions_of_its_borders():
    pairs = {(out.code, into.code) for out, into in directions_owned_by("ES")}

    assert pairs == {("ES", "PT"), ("PT", "ES"), ("ES", "FR"), ("FR", "ES")}
    assert directions_owned_by("IT-SARD") == []  # its one border is IT-CSUD's
