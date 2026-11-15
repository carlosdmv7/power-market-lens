"""The two charts are read one above the other, so they must share an x domain."""

from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "app"))

import charts  # noqa: E402
import mix  # noqa: E402


def x_domains(chart) -> list:
    spec = chart.to_dict()
    return [
        layer["encoding"]["x"]["scale"]["domain"]
        for layer in spec["layer"]
        if "x" in layer.get("encoding", {})
    ]


def test_price_and_mix_charts_share_the_time_domain():
    hours = pd.date_range("2026-10-14 00:00", "2026-10-20 23:00", freq="h")
    prices = pd.DataFrame({"time": hours, "price_eur_mwh": 50.0})
    generation = pd.DataFrame(
        {
            "time": hours[:130],
            "hour_start_utc": hours[:130],
            "psr_type": "B16",
            "display_group": "Solar",
            "generation_mw": 9000.0,
        }
    )
    domain = (hours[0], hours[-1])

    price_domains = x_domains(charts.price_chart(prices, domain))
    mix_domains = x_domains(charts.mix_chart(mix.grouped_mix(generation), domain))

    expected = [
        {"year": 2026, "month": 10, "date": 14, "hours": 0},
        {"year": 2026, "month": 10, "date": 20, "hours": 23},
    ]
    assert price_domains and mix_domains
    assert all(d == expected for d in price_domains + mix_domains)


FEATURES = [
    {"properties": {"zone_code": code, "role": role, "anchor_lon": lon, "anchor_lat": lat}}
    for code, role, lon, lat in [
        ("ES", "modelled", -3.5, 40.0),
        ("FR", "modelled", 2.2, 46.7),
        ("GB", "neighbour", -1.9, 54.3),
    ]
] + [{"properties": {"zone_code": None, "role": "context", "anchor_lon": None, "anchor_lat": None}}]


def test_flow_arrows_point_at_the_importer():
    flows = pd.DataFrame(
        {"source": ["ES", "GB"], "target": ["FR", "FR"], "net_mw": [800.0, 300.0], "n_hours": 24}
    )

    segments = charts.flow_segments(FEATURES, flows).set_index("source")

    # ES → FR runs north-east, GB → FR south-south-east: clockwise from north.
    assert 0 < segments.loc["ES", "bearing"] < 90
    assert 90 < segments.loc["GB", "bearing"] < 180
    # The head sits 60% of the way, towards the importer.
    assert segments.loc["ES", "head_lon"] == -3.5 + 0.6 * (2.2 - -3.5)


def test_flows_without_a_direction_or_a_shape_are_not_drawn():
    flows = pd.DataFrame(
        {"source": ["ES", "XX"], "target": ["FR", "FR"], "net_mw": [0.4, 500.0], "n_hours": 24}
    )

    assert charts.flow_segments(FEATURES, flows).empty


SPLIT = pd.DataFrame(
    {
        "country_name": ["Spain", "Netherlands"],
        "price_excl_taxes_eur_kwh": [0.1875, 0.5],
        "taxes_and_levies_eur_kwh": [0.03125, -0.75],
        "vat_eur_kwh": [0.03125, -0.03125],
        "total": [0.25, -0.28125],
    }
)


def _layer_data(chart, mark: str) -> list[dict]:
    spec = chart.to_dict()
    layer = next(
        layer
        for layer in spec["layer"]
        if (layer["mark"] if isinstance(layer["mark"], str) else layer["mark"]["type"]) == mark
    )
    return spec["datasets"][layer["data"]["name"]]


def test_part_bars_mark_the_total_and_label_past_the_bar():
    chart = charts.part_bars(SPLIT, charts.SPLIT_PARTS, total_title="Price")

    labels = {row["country_name"]: row for row in _layer_data(chart, "text")}
    # Spain's bar ends at its total; the Dutch bar ends at 0.5, far right of
    # its negative total, so the label goes by the bar and the tick shows the total.
    assert labels["Spain"]["label_x"] == 0.25
    assert labels["Netherlands"]["label_x"] == 0.5
    assert {row["total"] for row in _layer_data(chart, "tick")} == {0.25, -0.28125}
    spec = chart.to_dict()
    assert spec["layer"][0]["mark"]["type"] == "rule"  # the zero line, as a part is negative
    # Dearest first.
    bars = next(layer for layer in spec["layer"] if layer["mark"]["type"] == "bar")
    assert bars["encoding"]["y"]["sort"] == ["Spain", "Netherlands"]


def test_a_part_keeps_its_colour_in_both_breakdowns():
    split = {label: color for _, label, color in charts.SPLIT_PARTS}
    components = {label: color for _, label, color in charts.COMPONENT_PARTS}

    assert split["Taxes and levies"] == components["Taxes and levies"]
    assert split["VAT"] == components["VAT"]
    # The price before taxes splits into energy (same colour) and network.
    assert split["Before taxes"] == components["Energy and supply"]


def test_price_history_names_only_the_highlighted_country():
    history = pd.DataFrame(
        {
            "country_name": ["Spain", "Spain", "France", "France"],
            "period_code": ["2025-S2", "2026-S1"] * 2,
            "period_start_date": pd.to_datetime(["2025-07-01", "2026-01-01"] * 2),
            "price_incl_all_taxes_eur_kwh": [0.25, 0.26, 0.24, 0.23],
        }
    )

    names = _layer_data(charts.price_history_chart(history, "Spain"), "text")

    assert [row["country_name"] for row in names] == ["Spain"]
    assert names[0]["period_code"] == "2026-S1"
