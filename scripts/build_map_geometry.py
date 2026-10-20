# /// script
# requires-python = ">=3.13"
# dependencies = ["shapely==2.1.2"]
# ///
"""Build the bidding-zone polygons the app's map draws (ADR-005).

    uv run scripts/build_map_geometry.py

Bidding zones are not administrative units, so no free dataset ships them.
They are assembled here from Natural Earth's admin-1 units (public domain),
pinned to one release, and written to app/assets/map_zones.geojson, which is
committed: the app reads a file and never needs shapely. Re-run only to
change a zone's definition; the output is deterministic for a given release.

Three kinds of feature:
- modelled: the twelve zones the pipeline loads (zones.ZONES);
- neighbour: the zones they exchange power with (zones.NEIGHBOURS);
- context: other countries in view, drawn as land without data.
"""

from collections import defaultdict
import json
from pathlib import Path
import urllib.request

import shapely
from shapely.geometry import box, mapping, shape

NATURAL_EARTH = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson"
ADMIN_1 = f"{NATURAL_EARTH}/ne_10m_admin_1_states_provinces.geojson"
OUTPUT = Path(__file__).parents[1] / "app" / "assets" / "map_zones.geojson"

# Mainland Europe and its islands; drops overseas territories, the Canaries,
# the Azores, Madeira and the Arctic: the map needs NO-2 and SE-4, not the
# rest of Scandinavia.
VIEW = box(-10.5, 34.5, 30.0, 63.0)
TOLERANCE_DEG = 0.03  # ~3 km: invisible at the app's zoom
MIN_PART_DEG2 = 0.003  # ~25 km²: drops islets, keeps Malta (~0.03)
DECIMALS = 3

# zone code → (country ISO3, admin-1 field, values). No field: the whole
# country. Italy's zones follow Terna's regions since 2021 (Umbria in Centre-
# South, Molise in South, Calabria its own zone). NO-2 and SE-4 follow the
# counties that best match: their real borders cut through counties.
ZONE_UNITS: dict[str, list[tuple[str, str | None, set[str] | None]]] = {
    "ES": [("ESP", None, None)],
    "PT": [("PRT", None, None)],
    # Corsica is not on France's continental grid; it trades with Sardinia.
    "FR": [("FRA", "region-not", {"Corse"})],
    "DE-LU": [("DEU", None, None), ("LUX", None, None)],
    "NL": [("NLD", None, None)],
    "IT-NORD": [
        (
            "ITA",
            "region",
            {
                "Piemonte",
                "Valle d'Aosta",
                "Liguria",
                "Lombardia",
                "Trentino-Alto Adige",
                "Veneto",
                "Friuli-Venezia Giulia",
                "Emilia-Romagna",
            },
        )
    ],
    "IT-CNOR": [("ITA", "region", {"Toscana", "Marche"})],
    "IT-CSUD": [("ITA", "region", {"Umbria", "Lazio", "Abruzzo", "Campania"})],
    "IT-SUD": [("ITA", "region", {"Molise", "Apulia", "Basilicata"})],
    "IT-CALA": [("ITA", "region", {"Calabria"})],
    "IT-SICI": [("ITA", "region", {"Sicily"})],
    "IT-SARD": [("ITA", "region", {"Sardegna"})],
    "AT": [("AUT", None, None)],
    "BE": [("BEL", None, None)],
    "CH": [("CHE", None, None)],
    "CZ": [("CZE", None, None)],
    "DK-1": [("DNK", "name", {"Nordjylland", "Midtjylland", "Syddanmark"})],
    "DK-2": [("DNK", "name", {"Hovedstaden", "Sjaælland"})],  # sic, in Natural Earth
    "GB": [("GBR", "geonunit-not", {"Northern Ireland"})],
    "GR": [("GRC", None, None)],
    "ME": [("MNE", None, None)],
    "MT": [("MLT", None, None)],
    "NO-2": [("NOR", "name", {"Rogaland", "Vest-Agder", "Aust-Agder", "Telemark"})],
    "PL": [("POL", None, None)],
    "SE-4": [("SWE", "name", {"Skåne", "Blekinge", "Kronoberg"})],
    "SI": [("SVN", None, None)],
}
MODELLED = {
    "ES",
    "PT",
    "FR",
    "DE-LU",
    "NL",
    "IT-NORD",
    "IT-CNOR",
    "IT-CSUD",
    "IT-SUD",
    "IT-CALA",
    "IT-SICI",
    "IT-SARD",
}
# Spain's North African cities are outside the Iberian grid.
EXCLUDED_UNITS = {"Ceuta", "Melilla"}


def download(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - pinned https URL
        return json.load(response)


def matches(properties: dict, iso3: str, field: str | None, values: set[str] | None) -> bool:
    if properties["adm0_a3"] != iso3 or properties["name"] in EXCLUDED_UNITS:
        return False
    if field is None:
        return True
    if field.endswith("-not"):
        return properties[field.removesuffix("-not")] not in values
    return properties[field] in values


def without_islets(geometry: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(geometry) if part.area >= MIN_PART_DEG2]
    return shapely.union_all(parts)


def assemble(admin_1: dict) -> tuple[dict, dict]:
    """Zones from their units; every other unit in view, dissolved by country.

    Both come from the same admin-1 layer, so a zone and the rest of its
    country (Northern Ireland, Corsica, northern Norway) share one coastline.
    """
    zone_parts: dict[str, list] = defaultdict(list)
    context_parts: dict[str, list] = defaultdict(list)
    for feature in admin_1["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.intersects(VIEW):
            continue
        properties = feature["properties"]
        codes = [
            code
            for code, rules in ZONE_UNITS.items()
            if any(matches(properties, *rule) for rule in rules)
        ]
        if codes:
            zone_parts[codes[0]].append(geometry)
        elif properties["name"] not in EXCLUDED_UNITS:
            context_parts[properties["admin"]].append(geometry)
    missing = set(ZONE_UNITS) - set(zone_parts)
    if missing:
        raise SystemExit(f"No admin-1 units matched for {sorted(missing)}")

    def dissolve(parts: dict[str, list]) -> dict[str, shapely.Geometry]:
        shapes = {
            name: without_islets(shapely.union_all(geoms).intersection(VIEW))
            for name, geoms in parts.items()
        }
        return {name: geometry for name, geometry in shapes.items() if not geometry.is_empty}

    return dissolve(zone_parts), dissolve(context_parts)


def main() -> None:
    zones, context = assemble(download(ADMIN_1))

    names = list(zones) + list(context)
    # One coverage simplification: shared borders stay shared, so neighbouring
    # zones neither overlap nor open gaps between them.
    simplified = shapely.coverage_simplify(
        [*zones.values(), *context.values()], TOLERANCE_DEG, simplify_boundary=True
    )
    features = []
    for name, geometry in zip(names, simplified, strict=True):
        role = "context" if name in context else "modelled" if name in MODELLED else "neighbour"
        rounded = shapely.set_precision(geometry, 10**-DECIMALS)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "zone_code": name if role != "context" else None,
                    "name": name,
                    "role": role,
                },
                "geometry": mapping(rounded),
            }
        )
    OUTPUT.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))
        + "\n"
    )
    print(
        f"{OUTPUT.name}: {len(zones)} zones, {len(context)} context, {OUTPUT.stat().st_size:,} bytes"
    )


if __name__ == "__main__":
    main()
