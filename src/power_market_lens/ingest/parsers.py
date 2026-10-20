"""Parse ENTSO-E Transparency Platform XML into validated rows.

We parse the raw XML ourselves instead of using entsoe-py's pandas client,
which resamples series to one resolution. Here every point keeps its native
resolution (ADR-003), and the two traps of the format are handled explicitly:

- **Curve type A03** omits a point whose value repeats the previous one. The
  missing positions must be filled forward up to the end of the period, or
  those intervals silently disappear.
- **Namespaces** carry a schema version that ENTSO-E bumps from time to time,
  so elements are matched by local name, never by namespace.
- **Several day-ahead auctions** can share one zone: DE-LU publishes SDAC as
  classification sequence 1 and a second auction as sequence 2, at different
  prices (and, before October 2025, different resolutions). A document mixing
  sequences is refused; the zone's `sdac_sequence` must select one.

Format reference: ENTSO-E "Introduction of different Timeseries possibilities
(curvetypes)" and the Transparency Platform API guide.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
import re
import xml.etree.ElementTree as ET

from power_market_lens.ingest.models import (
    CrossBorderFlowRow,
    DayAheadPriceRow,
    GenerationRow,
    TotalLoadRow,
)

# Two document families: prices and flows are publication documents, generation
# and load are GL (generation and load) documents. Within a family, <type>
# tells them apart, so every parser checks it.
PUBLICATION_DOCUMENT = "Publication_MarketDocument"
GL_DOCUMENT = "GL_MarketDocument"
ACKNOWLEDGEMENT_DOCUMENT = "Acknowledgement_MarketDocument"

_RESOLUTION = re.compile(r"^PT(?P<value>\d+)(?P<unit>[MH])$")


class EntsoeDocumentError(ValueError):
    """The response is not the document we asked for, or is malformed."""


def parse_day_ahead_prices(xml_text: str) -> list[DayAheadPriceRow]:
    root = _parse_root(xml_text, expected=PUBLICATION_DOCUMENT, document_type="A44")
    mrid, created_at = _document_metadata(root)

    sequences = {
        _text(series, "classificationSequence_AttributeInstanceComponent.position", default=None)
        for series in _children(root, "TimeSeries")
    }
    if len(sequences) > 1:
        labels = ", ".join(sorted(str(s) for s in sequences))
        raise EntsoeDocumentError(
            f"The document mixes day-ahead classification sequences ({labels}): more than one "
            "auction is published for this zone. Set the zone's sdac_sequence."
        )

    rows = []
    for series in _children(root, "TimeSeries"):
        zone = _text(series, "in_Domain.mRID")
        currency = _text(series, "currency_Unit.name")
        price_unit = _text(series, "price_Measure_Unit.name")
        curve_type = _text(series, "curveType", default="A01")
        for period in _children(series, "Period"):
            for start, resolution, value in _expand_period(period, "price.amount", curve_type):
                rows.append(
                    DayAheadPriceRow(
                        bidding_zone_eic=zone,
                        period_start_utc=start,
                        resolution_minutes=resolution,
                        price_eur_mwh=value,
                        currency=currency,
                        price_unit=price_unit,
                        document_mrid=mrid,
                        document_created_at=created_at,
                    )
                )
    return rows


def parse_generation_per_type(xml_text: str) -> list[GenerationRow]:
    root = _parse_root(xml_text, expected=GL_DOCUMENT, document_type="A75")
    mrid, created_at = _document_metadata(root)

    rows = []
    for series in _children(root, "TimeSeries"):
        # Generation is reported *into* the zone; consumption by generation
        # units (pumping) *out of* it. A series carries exactly one of the two.
        in_zone = _text(series, "inBiddingZone_Domain.mRID", default=None)
        out_zone = _text(series, "outBiddingZone_Domain.mRID", default=None)
        if (in_zone is None) == (out_zone is None):
            raise EntsoeDocumentError(
                "A generation time series must name exactly one of inBiddingZone_Domain "
                "and outBiddingZone_Domain"
            )
        zone, direction = (in_zone, "generation") if in_zone else (out_zone, "consumption")

        _expect_megawatts(series)
        psr_type = _text(_child(series, "MktPSRType"), "psrType")
        curve_type = _text(series, "curveType", default="A01")
        for period in _children(series, "Period"):
            for start, resolution, value in _expand_period(period, "quantity", curve_type):
                rows.append(
                    GenerationRow(
                        bidding_zone_eic=zone,
                        psr_type=psr_type,
                        flow_direction=direction,
                        period_start_utc=start,
                        resolution_minutes=resolution,
                        quantity_mw=value,
                        document_mrid=mrid,
                        document_created_at=created_at,
                    )
                )
    return rows


def parse_total_load(xml_text: str) -> list[TotalLoadRow]:
    """Actual total load (process A16) or its day-ahead forecast (A01)."""
    root = _parse_root(xml_text, expected=GL_DOCUMENT, document_type="A65")
    mrid, created_at = _document_metadata(root)
    process_type = _text(root, "process.processType")
    if process_type not in {"A16", "A01"}:
        raise EntsoeDocumentError(
            f"Expected total load as realised (A16) or day-ahead forecast (A01), "
            f"got process type {process_type!r}"
        )

    rows = []
    for series in _children(root, "TimeSeries"):
        # Load is reported as consumption out of the zone.
        zone = _text(series, "outBiddingZone_Domain.mRID")
        _expect_megawatts(series)
        curve_type = _text(series, "curveType", default="A01")
        for period in _children(series, "Period"):
            for start, resolution, value in _expand_period(period, "quantity", curve_type):
                rows.append(
                    TotalLoadRow(
                        bidding_zone_eic=zone,
                        process_type=process_type,
                        period_start_utc=start,
                        resolution_minutes=resolution,
                        load_mw=value,
                        document_mrid=mrid,
                        document_created_at=created_at,
                    )
                )
    return rows


def parse_crossborder_flows(xml_text: str) -> list[CrossBorderFlowRow]:
    """Physical flows (A11), one non-negative series per direction."""
    root = _parse_root(xml_text, expected=PUBLICATION_DOCUMENT, document_type="A11")
    mrid, created_at = _document_metadata(root)

    rows = []
    for series in _children(root, "TimeSeries"):
        out_zone = _text(series, "out_Domain.mRID")
        in_zone = _text(series, "in_Domain.mRID")
        _expect_megawatts(series)
        curve_type = _text(series, "curveType", default="A01")
        for period in _children(series, "Period"):
            for start, resolution, value in _expand_period(period, "quantity", curve_type):
                rows.append(
                    CrossBorderFlowRow(
                        out_zone_eic=out_zone,
                        in_zone_eic=in_zone,
                        period_start_utc=start,
                        resolution_minutes=resolution,
                        flow_mw=value,
                        document_mrid=mrid,
                        document_created_at=created_at,
                    )
                )
    return rows


def _expect_megawatts(series: ET.Element) -> None:
    unit = _text(series, "quantity_Measure_Unit.name")
    if unit != "MAW":
        raise EntsoeDocumentError(f"Expected quantities in MAW (megawatts), got {unit!r}")


# ── Period expansion ──────────────────────────────────────────────────────────


def _expand_period(
    period: ET.Element, value_tag: str, curve_type: str
) -> Iterator[tuple[datetime, int, float]]:
    """Yield (interval start in UTC, resolution in minutes, value) per interval."""
    interval = _child(period, "timeInterval")
    start = _parse_utc(_text(interval, "start"))
    end = _parse_utc(_text(interval, "end"))
    resolution = _parse_resolution(_text(period, "resolution"))
    step = timedelta(minutes=resolution)

    n_intervals, remainder = divmod(end - start, step)
    if remainder or n_intervals <= 0:
        raise EntsoeDocumentError(
            f"Period {start}–{end} is not a whole number of {resolution}-minute intervals"
        )

    values: dict[int, float] = {}
    for point in _children(period, "Point"):
        position = int(_text(point, "position"))
        if not 1 <= position <= n_intervals:
            raise EntsoeDocumentError(
                f"Point position {position} outside the period's 1..{n_intervals}"
            )
        values[position] = float(_text(point, value_tag))

    if curve_type == "A03":
        # Variable-sized blocks: a point holds its value until the next point.
        if 1 not in values:
            raise EntsoeDocumentError("An A03 period must start with a point at position 1")
        current = values[1]
        for position in range(1, n_intervals + 1):
            current = values.get(position, current)
            yield start + (position - 1) * step, resolution, current
    elif curve_type == "A01":
        # Fixed-size blocks: every published point stands on its own. A missing
        # position is a genuine gap, left for the completeness tests to find.
        for position in sorted(values):
            yield start + (position - 1) * step, resolution, values[position]
    else:
        raise EntsoeDocumentError(f"Unsupported curve type {curve_type!r}")


def _parse_resolution(text: str) -> int:
    match = _RESOLUTION.match(text)
    if not match:
        raise EntsoeDocumentError(f"Unsupported resolution {text!r}")
    value = int(match["value"])
    return value * 60 if match["unit"] == "H" else value


def _parse_utc(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise EntsoeDocumentError(f"Timestamp {text!r} has no time zone")
    return parsed


# ── XML helpers (namespace-agnostic) ──────────────────────────────────────────


def _parse_root(xml_text: str, expected: str, document_type: str) -> ET.Element:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise EntsoeDocumentError(f"Response is not valid XML: {error}") from error

    name = _local(root.tag)
    if name == ACKNOWLEDGEMENT_DOCUMENT:
        reason = _text(_child(root, "Reason"), "text", default="no reason given")
        raise EntsoeDocumentError(f"ENTSO-E acknowledged the request without data: {reason}")
    if name != expected:
        raise EntsoeDocumentError(f"Expected a {expected}, got a {name}")
    found_type = _text(root, "type")
    if found_type != document_type:
        raise EntsoeDocumentError(f"Expected document type {document_type}, got {found_type}")
    return root


def _document_metadata(root: ET.Element) -> tuple[str, datetime]:
    return _text(root, "mRID"), _parse_utc(_text(root, "createdDateTime"))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> Iterator[ET.Element]:
    return (child for child in element if _local(child.tag) == name)


def _child(element: ET.Element, name: str) -> ET.Element:
    child = next(_children(element, name), None)
    if child is None:
        raise EntsoeDocumentError(f"<{_local(element.tag)}> has no <{name}>")
    return child


_REQUIRED = object()


def _text(element: ET.Element, name: str, default=_REQUIRED):
    child = next(_children(element, name), None)
    if child is None or child.text is None:
        if default is _REQUIRED:
            raise EntsoeDocumentError(f"<{_local(element.tag)}> has no <{name}>")
        return default
    return child.text.strip()
