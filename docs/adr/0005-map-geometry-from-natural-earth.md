# ADR-005 — Build bidding-zone shapes from Natural Earth admin-1 units

- **Status:** accepted
- **Date:** 2026-10-20

## Context

The app's map colours each bidding zone and draws the flows between them.
Bidding zones are not administrative units, and no free, licence-clear
dataset publishes their polygons. Most zones are whole countries. Five are not:

- Italy has seven zones, defined by Terna as groups of regions (reconfigured
  on 2021-01-01: Umbria moved to Centre-South, Molise to South, Calabria
  became a zone of its own);
- DE-LU joins two countries;
- DK-1 and DK-2 split Denmark at the Great Belt;
- NO-2 and SE-4 are parts of Norway and Sweden whose borders follow the grid,
  not counties;
- GB excludes Northern Ireland, which trades in the all-island market.

The app must stay light: it runs on Streamlit Community Cloud and should not
depend on a GIS stack.

The approach was chosen on 2026-10-19: Natural Earth and a script.

## Decision

`scripts/build_map_geometry.py` assembles each zone from Natural Earth's
10m admin-1 units. It is a PEP 723 script, so its one dependency (shapely)
never enters the project. It writes `app/assets/map_zones.geojson`, which is
committed, and the app only reads that file.

- **One source, one release:** v5.1.2, pinned in the URL. Every shape comes
  from the same layer, zones and context alike, so a zone and the rest of its
  country (Northern Ireland, Corsica, central Sweden) share one coastline.
- **One coverage simplification** (`shapely.coverage_simplify`, 0.03°), so
  shared borders stay shared: no gaps or overlaps between zones.
- Islets under ~25 km² are dropped. Malta stays.
- **The view runs from Portugal to Greece and from Malta to 63°N.** The
  Canaries, Azores, Madeira, Ceuta, Melilla and overseas territories are left
  out: none of them are in these bidding zones.
- **Corsica is left out of FR.** It is not on the continental French grid; it
  is linked to Sardinia and Italy.
- **Three feature roles:**
  - modelled: the twelve zones;
  - neighbour: the fourteen zones they exchange power with;
  - context: other land in view, drawn without data.
- A test keeps the file's zone codes equal to `zones.ZONES` and
  `zones.NEIGHBOURS`.

The output is deterministic for a given release, so re-running the script
gives the same bytes.

## Alternatives considered

- **Electricity Maps' zone shapes.** Closer grid borders for NO-2 and SE-4,
  but a licence to work through for a derived, committed file, and a
  dependency on another project's repository layout. Natural Earth is public
  domain.
- **A GIS stack in the app** (geopandas). It would build the shapes at run
  time, but it is heavy for Streamlit Cloud, slow to start, and recomputes
  something that changes only when a zone is redefined.
- **Country shapes only.** That breaks the moment the map shows Italy's seven
  zones, which have most of the internal flows.

## Consequences

- NO-2 is Rogaland, the two Agder counties and Telemark, and SE-4 is Skåne,
  Blekinge and Kronoberg. Both are close, not exact; they are neighbours,
  drawn for their flows only.
- Natural Earth's admin-1 names are taken as they are, including a typo
  (Sjaælland), so a newer release may need the mapping touched up. The script
  stops when a zone matches no unit.
- The file is about 230 KB, read once per app session.
- Redefining a zone means editing `ZONE_UNITS` and re-running
  `make map-geometry`.
