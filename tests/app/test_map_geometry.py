"""The committed map geometry (scripts/build_map_geometry.py) against zones.py."""

import json
from pathlib import Path

from power_market_lens.zones import NEIGHBOURS, ZONES

GEOMETRY = Path(__file__).parents[2] / "app" / "assets" / "map_zones.geojson"


def features_by_role() -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {"modelled": set(), "neighbour": set(), "context": set()}
    for feature in json.loads(GEOMETRY.read_text())["features"]:
        properties = feature["properties"]
        roles[properties["role"]].add(properties["zone_code"] or properties["name"])
    return roles


def test_every_zone_and_neighbour_has_a_shape_in_its_role():
    # A zone added to zones.py without re-running the script would be missing
    # from the map, not an error.
    roles = features_by_role()

    assert roles["modelled"] == set(ZONES)
    assert roles["neighbour"] == set(NEIGHBOURS)


def test_every_shape_is_a_non_empty_polygon():
    for feature in json.loads(GEOMETRY.read_text())["features"]:
        geometry = feature["geometry"]
        assert geometry["type"] in {"Polygon", "MultiPolygon"}, feature["properties"]
        assert geometry["coordinates"], feature["properties"]
