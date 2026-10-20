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
    generation = pd.DataFrame({"time": hours[:130], "psr_type": "B16", "generation_mw": 9000.0})
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
