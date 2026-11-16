"""The page's two charts, built on the brand Altair theme registered by theme.py.

The charts are read one above the other, price against mix, so both take the
same time domain: the same hour sits at the same x in each, although actual
generation stops at the latest published hour while prices run into tomorrow.

Both share the same hover: a vertical rule follows the pointer to the nearest
hour, and a tooltip gives the exact values, so no number has to be read off an
axis.
"""

import math

import altair as alt
from mix import GROUP_COLORS, GROUP_ORDER
import pandas as pd
from theme import AMBER_500, INK, INK_MUTED, PETROL_900, RUST_500, SURFACE, TEAL_500

TIME_FORMAT = "%a %d %b, %H:%M"
# One tick per day: at the default density, day-formatted ticks every 12 hours
# printed each date twice.
DAY_AXIS = alt.Axis(format="%d %b", tickCount={"interval": "day", "step": 1})
# The y axes get one fixed width so both plot areas start at the same x:
# otherwise "40,000" pushes the mix chart right of the price chart's "300".
Y_EXTENT = 52


def y_axis() -> alt.Axis:
    return alt.Axis(minExtent=Y_EXTENT, maxExtent=Y_EXTENT)


def _local(moment: pd.Timestamp) -> alt.DateTime:
    # Vega reads the data's naive timestamps as wall-clock time, and so a
    # DateTime without utc=True: the domain lands on the same hours as the data.
    return alt.DateTime(year=moment.year, month=moment.month, date=moment.day, hours=moment.hour)


def time_x(domain: tuple[pd.Timestamp, pd.Timestamp], **kwargs) -> alt.X:
    """The shared x encoding: hourly times over a fixed (first, last) hour."""
    scale = alt.Scale(domain=[_local(domain[0]), _local(domain[1])])
    return alt.X("time:T", scale=scale, **kwargs)


def _hourly_line(
    data: pd.DataFrame,
    field: str,
    *,
    unit: str,
    color: str,
    number_format: str,
    domain: tuple[pd.Timestamp, pd.Timestamp],
) -> list[alt.Chart]:
    """One hourly series with the shared hover: rule, dot and exact tooltip.

    The October DST change repeats 02:00 local: its two values are drawn as
    their mean, one point per x, as the mix chart does with generation.
    """
    data = data.groupby("time", as_index=False)[field].mean()
    hover = alt.selection_point(
        fields=["time"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    base = alt.Chart(data).encode(x=time_x(domain, title=None, axis=DAY_AXIS))
    line = base.mark_line(stroke=color, strokeWidth=2, clip=True).encode(
        y=alt.Y(f"{field}:Q", title=unit, axis=y_axis()),
    )
    targets = (
        base.mark_point(opacity=0, size=200, clip=True)
        .encode(
            y=f"{field}:Q",
            tooltip=[
                alt.Tooltip("time:T", title="Hour", format=TIME_FORMAT),
                alt.Tooltip(f"{field}:Q", title=unit, format=number_format),
            ],
        )
        .add_params(hover)
    )
    rule = base.mark_rule(color=INK_MUTED, clip=True).transform_filter(hover)
    dot = line.mark_point(
        filled=True, size=70, color=color, stroke=SURFACE, strokeWidth=2, clip=True
    ).transform_filter(hover)
    return [line, rule, dot, targets]


def price_chart(prices: pd.DataFrame, domain: tuple[pd.Timestamp, pd.Timestamp]) -> alt.LayerChart:
    """Hourly price, one series. A zero line appears when prices go negative."""
    layers = _hourly_line(
        prices,
        "price_eur_mwh",
        unit="€/MWh",
        color=RUST_500,
        number_format=",.2f",
        domain=domain,
    )
    if (prices["price_eur_mwh"] < 0).any():
        zero = (
            alt.Chart(pd.DataFrame({"y": [0]}))
            .mark_rule(color=INK_MUTED, strokeDash=[4, 3])
            .encode(y="y:Q")
        )
        layers.insert(0, zero)
    return alt.layer(*layers).properties(height=280)


def intensity_chart(
    metrics: pd.DataFrame, domain: tuple[pd.Timestamp, pd.Timestamp]
) -> alt.LayerChart:
    """Hourly carbon intensity of production, in petrol: a different measure
    from the price (rust), on the same time axis."""
    layers = _hourly_line(
        metrics,
        "carbon_intensity_g_per_kwh",
        unit="gCO₂eq/kWh",
        color=PETROL_900,
        number_format=",.0f",
        domain=domain,
    )
    return alt.layer(*layers).properties(height=220)


def conditions_chart(
    conditions: pd.DataFrame, domain: tuple[pd.Timestamp, pd.Timestamp]
) -> alt.LayerChart:
    """How unusual each hour was (ADR-008), on the shared time axis.

    1 is the control limit: a dashed rule. The line is muted ink, because most
    hours are unremarkable; hours beyond the limit are marked in rust, the one
    colour the eye should go to. The hover names what stood out.
    """
    data = conditions.groupby("time", as_index=False).agg(
        unusualness=("unusualness", "max"),
        is_unusual=("is_unusual", "max"),
        what=("what", "first"),
    )
    hover = alt.selection_point(
        fields=["time"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    x = time_x(domain, title=None, axis=DAY_AXIS)
    y = alt.Y("unusualness:Q", title="× control limit", axis=y_axis())
    base = alt.Chart(data).encode(x=x)
    limit = (
        alt.Chart(pd.DataFrame({"y": [1.0]}))
        .mark_rule(color=INK_MUTED, strokeDash=[4, 3])
        .encode(y="y:Q")
    )
    line = base.mark_line(stroke=INK_MUTED, strokeWidth=1.5, clip=True).encode(y=y)
    flagged = (
        base.mark_point(
            filled=True, size=40, color=RUST_500, stroke=SURFACE, strokeWidth=1, clip=True
        )
        .encode(y="unusualness:Q")
        .transform_filter("datum.is_unusual")
    )
    targets = (
        base.mark_point(opacity=0, size=200, clip=True)
        .encode(
            y="unusualness:Q",
            tooltip=[
                alt.Tooltip("time:T", title="Hour", format=TIME_FORMAT),
                alt.Tooltip("unusualness:Q", title="× control limit", format=".2f"),
                alt.Tooltip("what:N", title="Stood out"),
            ],
        )
        .add_params(hover)
    )
    rule = base.mark_rule(color=INK_MUTED, clip=True).transform_filter(hover)
    return alt.layer(limit, line, flagged, rule, targets).properties(height=200)


LOAD_SERIES = {"Actual": PETROL_900, "Day-ahead forecast": TEAL_500}


def load_chart(load: pd.DataFrame, domain: tuple[pd.Timestamp, pd.Timestamp]) -> alt.LayerChart:
    """Actual load (solid) against the TSO's day-ahead forecast (dashed).

    Two series, so a legend; the forecast is dashed as well as a second hue,
    so the pair never rests on colour alone. The forecast runs into tomorrow;
    the actual stops at the latest reported hour. The repeated October DST
    hour is drawn as its mean, as in the other charts.
    """
    load = load.groupby("time", as_index=False)[["actual_load_mw", "forecast_load_mw"]].mean()
    hover = alt.selection_point(
        fields=["time"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    long = load.melt(
        id_vars="time",
        value_vars=["actual_load_mw", "forecast_load_mw"],
        var_name="series",
        value_name="load_mw",
    ).dropna(subset=["load_mw"])
    long["series"] = long["series"].map(
        {"actual_load_mw": "Actual", "forecast_load_mw": "Day-ahead forecast"}
    )
    lines = (
        alt.Chart(long)
        .mark_line(strokeWidth=2, clip=True)
        .encode(
            x=time_x(domain, title=None, axis=DAY_AXIS),
            y=alt.Y("load_mw:Q", title="MW", axis=y_axis()),
            color=alt.Color(
                "series:N",
                title=None,
                scale=alt.Scale(domain=list(LOAD_SERIES), range=list(LOAD_SERIES.values())),
                legend=alt.Legend(orient="top"),
            ),
            strokeDash=alt.StrokeDash(
                "series:N",
                scale=alt.Scale(domain=list(LOAD_SERIES), range=[[1, 0], [5, 3]]),
                legend=None,
            ),
        )
    )
    targets = (
        alt.Chart(load)
        .mark_rule(opacity=0, strokeWidth=12)
        .encode(
            x=time_x(domain),
            tooltip=[
                alt.Tooltip("time:T", title="Hour", format=TIME_FORMAT),
                alt.Tooltip("actual_load_mw:Q", title="Actual (MW)", format=",.0f"),
                alt.Tooltip("forecast_load_mw:Q", title="Forecast (MW)", format=",.0f"),
            ],
        )
        .add_params(hover)
    )
    rule = (
        alt.Chart(load).mark_rule(color=INK_MUTED).encode(x=time_x(domain)).transform_filter(hover)
    )
    return alt.layer(lines, rule, targets).properties(height=240)


def mix_chart(mix: pd.DataFrame, domain: tuple[pd.Timestamp, pd.Timestamp]) -> alt.LayerChart:
    """Stacked generation by group, in the fixed order and colours of mix.GROUPS."""
    hover = alt.selection_point(
        fields=["time"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    color = alt.Color(
        "group:N",
        title=None,
        scale=alt.Scale(domain=GROUP_ORDER, range=GROUP_COLORS),
        sort=GROUP_ORDER,
    )
    # Vega stacks in ascending order from the baseline: rank 0 (nuclear) at the bottom.
    order = alt.Order("group_rank:Q", sort="ascending")
    data = mix.assign(group_rank=mix["group"].cat.codes)

    # A 1px surface stroke on each band gives the 2px gap between neighbours.
    areas = (
        alt.Chart(data)
        .mark_area(stroke=SURFACE, strokeWidth=1, opacity=1)
        .encode(
            x=time_x(domain, title=None, axis=DAY_AXIS),
            y=alt.Y(
                "generation_mw:Q",
                stack="zero",
                title="MW (average over the hour)",
                axis=y_axis(),
            ),
            color=color,
            order=order,
        )
    )
    # The tooltip reads one row per hour with every group as a column. Built in
    # pandas so a group with no output that hour reads 0, not NaN.
    wide = (
        mix.pivot_table(index="time", columns="group", values="generation_mw", observed=False)
        .reindex(columns=GROUP_ORDER)
        .fillna(0)
        .reset_index()
    )
    wide.columns = ["time", *GROUP_ORDER]
    targets = (
        alt.Chart(wide)
        .mark_rule(opacity=0, strokeWidth=12)
        .encode(
            x=time_x(domain),
            tooltip=[alt.Tooltip("time:T", title="Hour", format=TIME_FORMAT)]
            + [
                alt.Tooltip(f"{name}:Q", title=f"{name} (MW)", format=",.0f")
                for name in GROUP_ORDER
            ],
        )
        .add_params(hover)
    )
    rule = (
        alt.Chart(data).mark_rule(color=INK_MUTED).encode(x=time_x(domain)).transform_filter(hover)
    )
    return alt.layer(areas, rule, targets).properties(height=320)


# The brand's sequential ramp, sand to deep rust (.streamlit/config.toml,
# chartSequentialColors): one hue family, light to dark, for one magnitude.
PRICE_RAMP = ["#F6E2B3", "#E7A84E", "#D96C2C", "#A8501F", "#6E2F10"]
MAP_HEIGHT = 520


def price_scale(prices: pd.DataFrame) -> alt.Scale:
    """One scale for the map and the bars, so a colour means the same price in both."""
    low, high = prices["price_eur_mwh"].min(), prices["price_eur_mwh"].max()
    return alt.Scale(domain=[low, high], range=PRICE_RAMP, interpolate="lab")


def _mercator_y(lat: float) -> float:
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def flow_segments(features: list[dict], flows: pd.DataFrame) -> pd.DataFrame:
    """One row per border: anchors of both ends, and where and how to draw
    the arrowhead (60% of the way to the importer, pointing at it)."""
    anchors = {
        f["properties"]["zone_code"]: (f["properties"]["anchor_lon"], f["properties"]["anchor_lat"])
        for f in features
        if f["properties"]["role"] != "context"
    }
    rows = []
    for flow in flows.itertuples():
        # A border that netted to zero has no direction to draw.
        if flow.net_mw < 1 or flow.source not in anchors or flow.target not in anchors:
            continue
        (lon1, lat1), (lon2, lat2) = anchors[flow.source], anchors[flow.target]
        # The bearing on screen, in the map's Mercator projection: clockwise
        # from north, the angle Vega rotates a triangle-up by.
        bearing = math.degrees(math.atan2(lon2 - lon1, _mercator_y(lat2) - _mercator_y(lat1)))
        rows.append(
            {
                "source": flow.source,
                "target": flow.target,
                "net_mw": flow.net_mw,
                "lon1": lon1,
                "lat1": lat1,
                "lon2": lon2,
                "lat2": lat2,
                "head_lon": lon1 + 0.6 * (lon2 - lon1),
                "head_lat": lat1 + 0.6 * (lat2 - lat1),
                "bearing": bearing,
            }
        )
    return pd.DataFrame(rows)


def _flow_layers(segments: pd.DataFrame) -> list[alt.Chart]:
    tooltip = [
        alt.Tooltip("source:N", title="From"),
        alt.Tooltip("target:N", title="To"),
        alt.Tooltip("net_mw:Q", title="Mean net flow (MW)", format=",.0f"),
    ]
    base = alt.Chart(segments)
    lines = base.mark_rule(color=PETROL_900, opacity=0.85, strokeCap="round").encode(
        longitude="lon1:Q",
        latitude="lat1:Q",
        longitude2="lon2:Q",
        latitude2="lat2:Q",
        strokeWidth=alt.StrokeWidth(
            "net_mw:Q", scale=alt.Scale(range=[1, 7], zero=True), legend=None
        ),
        tooltip=tooltip,
    )
    heads = base.mark_point(
        shape="triangle-up", filled=True, color=PETROL_900, opacity=1, stroke=SURFACE, strokeWidth=1
    ).encode(
        longitude="head_lon:Q",
        latitude="head_lat:Q",
        angle=alt.Angle("bearing:Q", scale=None),
        size=alt.Size("net_mw:Q", scale=alt.Scale(range=[60, 260], zero=True), legend=None),
        tooltip=tooltip,
    )
    return [lines, heads]


def price_map(
    features: list[dict], prices: pd.DataFrame, flows: pd.DataFrame | None = None
) -> alt.LayerChart:
    """Each modelled zone filled by its day's mean price; all other land quiet.

    The land layer goes underneath, so a zone without a price that day still
    shows as land instead of vanishing (Vega drops marks with a null colour).
    Borders are drawn in the page colour: a gap between fills, not a line.
    """
    data = alt.Data(values=features)
    land = (
        alt.Chart(data)
        # Cool muted ink at low opacity: outside the warm price ramp, so its
        # palest step (sand) never reads as "no data".
        .mark_geoshape(fill=INK_MUTED, fillOpacity=0.16, stroke=SURFACE, strokeWidth=0.8)
        .encode(tooltip=[alt.Tooltip("properties.name:N", title="Area")])
        .transform_filter("datum.properties.role != 'modelled'")
    )
    zones = (
        alt.Chart(data)
        .mark_geoshape(stroke=SURFACE, strokeWidth=1)
        .transform_filter("datum.properties.role == 'modelled'")
        .transform_lookup(
            lookup="properties.zone_code",
            from_=alt.LookupData(
                prices,
                key="zone_code",
                fields=["price_eur_mwh", "min_price_eur_mwh", "max_price_eur_mwh"],
            ),
        )
        .encode(
            color=alt.Color(
                "price_eur_mwh:Q",
                scale=price_scale(prices),
                title="€/MWh",
                # Top left is open Atlantic in this view; bottom left is Iberia.
                legend=alt.Legend(orient="top-left", direction="horizontal", gradientLength=180),
            ),
            tooltip=[
                alt.Tooltip("properties.zone_code:N", title="Zone"),
                alt.Tooltip("price_eur_mwh:Q", title="Mean price (€/MWh)", format=",.2f"),
                alt.Tooltip("min_price_eur_mwh:Q", title="Cheapest hour (€/MWh)", format=",.2f"),
                alt.Tooltip("max_price_eur_mwh:Q", title="Dearest hour (€/MWh)", format=",.2f"),
            ],
        )
    )
    layers = [land, zones]
    if flows is not None and not flows.empty:
        segments = flow_segments(features, flows)
        if not segments.empty:
            layers += _flow_layers(segments)
    return alt.layer(*layers).project(type="mercator").properties(height=MAP_HEIGHT)


def price_ranking(prices: pd.DataFrame) -> alt.LayerChart:
    """The same prices as bars, dearest first: the map shows where, this shows how much."""
    base = alt.Chart(prices).encode(
        y=alt.Y("zone_code:N", sort="-x", title=None),
        x=alt.X("price_eur_mwh:Q", title="Mean day-ahead price (€/MWh)"),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.7}).encode(
        color=alt.Color("price_eur_mwh:Q", scale=price_scale(prices), legend=None),
        tooltip=[
            alt.Tooltip("zone_code:N", title="Zone"),
            alt.Tooltip("price_eur_mwh:Q", title="Mean price (€/MWh)", format=",.2f"),
        ],
    )
    labels = base.mark_text(align="left", dx=4, color=INK_MUTED).encode(
        text=alt.Text("price_eur_mwh:Q", format=",.0f")
    )
    return alt.layer(bars, labels).properties(height=MAP_HEIGHT)


# ── Household prices ──────────────────────────────────────────────────────────
# A part keeps its colour in both breakdowns: taxes and levies teal, VAT rust.
# The price before taxes (petrol) is what the yearly components split into
# energy and supply (petrol) and network costs (amber). In stacking order,
# every neighbouring pair passes the colour-vision and normal-vision
# separation checks of the portfolio's chart guide; amber is under 3:1 on the
# page, so every value is also in the tooltip and the data table.
EUR_KWH = ",.3f"
SPLIT_PARTS = [
    ("price_excl_taxes_eur_kwh", "Before taxes", PETROL_900),
    ("taxes_and_levies_eur_kwh", "Taxes and levies", TEAL_500),
    ("vat_eur_kwh", "VAT", RUST_500),
]
COMPONENT_PARTS = [
    ("energy_and_supply", "Energy and supply", PETROL_900),
    ("network", "Network costs", AMBER_500),
    ("taxes_and_levies", "Taxes and levies", TEAL_500),
    ("vat", "VAT", RUST_500),
]
BAR_ROW = 46  # px per country
COUNTRY_EXTENT = 96  # px for the country names


def part_bars(
    wide: pd.DataFrame, parts: list[tuple[str, str, str]], *, total_title: str
) -> alt.LayerChart:
    """One horizontal stacked bar per country: the parts of its price per kWh.

    `wide` has `country_name`, one column per part and `total`. A negative
    part (the Dutch energy-tax refund) stacks left of zero, so the bar's right
    end is not the price: a dark tick marks the total, and the label sits by
    whichever is further right. Countries are ranked by total, dearest first.
    """
    columns = [column for column, _, _ in parts]
    labels = {column: label for column, label, _ in parts}
    order = wide.sort_values("total", ascending=False)["country_name"].tolist()
    positive_end = wide[columns].clip(lower=0).sum(axis=1)
    wide = wide.assign(label_x=positive_end.where(positive_end > wide["total"], wide["total"]))
    long = wide.melt(
        id_vars="country_name", value_vars=columns, var_name="column", value_name="eur_kwh"
    ).merge(wide, on="country_name")
    long["part"] = long["column"].map(labels)
    long["rank"] = long["column"].map({column: i for i, column in enumerate(columns)})

    # A fixed label column: Vega measures the mono labels before the web font
    # has loaded, under-reserves, and cut "Netherlands" to "etherlands".
    y = alt.Y("country_name:N", sort=order, title=None, axis=alt.Axis(minExtent=COUNTRY_EXTENT))
    # Room on the right for the total's label past the longest bar.
    low = min(0.0, long["eur_kwh"].min() if not long.empty else 0.0)
    high = (wide["label_x"].max() if not wide.empty else 0.0) * 1.05
    x_scale = alt.Scale(domain=[low, high], nice=False)
    x_axis = alt.Axis(format=".2f", tickCount=8)
    tooltip = [alt.Tooltip("country_name:N", title="Country")]
    tooltip += [
        alt.Tooltip(f"{column}:Q", title=label, format=EUR_KWH) for column, label, _ in parts
    ]
    tooltip += [alt.Tooltip("total:Q", title=total_title, format=EUR_KWH)]
    # A 1px surface stroke on each segment gives the gap between neighbours.
    bars = (
        alt.Chart(long)
        .mark_bar(stroke=SURFACE, strokeWidth=1, cornerRadiusEnd=0, height={"band": 0.62})
        .encode(
            y=y,
            x=alt.X("eur_kwh:Q", stack="zero", title="€/kWh", scale=x_scale, axis=x_axis),
            color=alt.Color(
                "part:N",
                title=None,
                scale=alt.Scale(
                    domain=[label for _, label, _ in parts], range=[color for _, _, color in parts]
                ),
                sort=[label for _, label, _ in parts],
            ),
            order=alt.Order("rank:Q", sort="ascending"),
            tooltip=tooltip,
        )
    )
    base = alt.Chart(wide).encode(y=y)
    total = base.mark_tick(color=INK, thickness=3, size=BAR_ROW * 0.78).encode(
        x="total:Q", tooltip=tooltip[:1] + tooltip[-1:]
    )
    text = base.mark_text(align="left", dx=6, color=INK).encode(
        x="label_x:Q", text=alt.Text("total:Q", format=EUR_KWH)
    )
    layers = [bars, total, text]
    if (long["eur_kwh"] < 0).any():
        layers.insert(
            0, alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=INK_MUTED).encode(x="x:Q")
        )
    # The label past the longest bar needs room in pixels, not in €/kWh: a
    # share of the axis was not enough on a phone.
    return alt.layer(*layers).properties(
        height=BAR_ROW * len(order), padding={"left": 5, "top": 5, "bottom": 5, "right": 44}
    )


def price_history_chart(history: pd.DataFrame, highlight: str) -> alt.LayerChart:
    """The final price per half-year since 2007, one line per country.

    Six countries are more than the five categorical colours, so the chosen
    country is drawn in petrol and named at its last point, the others thin
    and muted; the hover names any line. Each half-year is drawn at its first day.
    """
    field = "price_incl_all_taxes_eur_kwh"
    hover = alt.selection_point(
        fields=["period_start_date"],
        nearest=True,
        on="pointerover",
        clear="pointerout",
        empty=False,
    )
    x = alt.X("period_start_date:T", title=None, axis=alt.Axis(format="%Y", tickCount="year"))
    y = alt.Y(f"{field}:Q", title="€/kWh, all taxes included")
    others = (
        alt.Chart(history[history["country_name"] != highlight])
        .mark_line(stroke=INK_MUTED, strokeWidth=1.5, opacity=0.45)
        .encode(x=x, y=y, detail="country_name:N")
    )
    chosen = history[history["country_name"] == highlight]
    line = alt.Chart(chosen).mark_line(stroke=PETROL_900, strokeWidth=2.5).encode(x=x, y=y)
    last = chosen.loc[[chosen["period_start_date"].idxmax()]] if not chosen.empty else chosen
    name = (
        alt.Chart(last)
        .mark_text(align="right", dy=-12, color=INK, fontWeight=600)
        .encode(x=x, y=y, text="country_name:N")
    )
    # Hover: a rule at the nearest half-year and a tooltip per country there.
    targets = (
        alt.Chart(history)
        .mark_point(opacity=0, size=160)
        .encode(
            x=x,
            y=y,
            tooltip=[
                alt.Tooltip("country_name:N", title="Country"),
                alt.Tooltip("period_code:N", title="Half-year"),
                alt.Tooltip(f"{field}:Q", title="€/kWh", format=EUR_KWH),
            ],
        )
        .add_params(hover)
    )
    rule = alt.Chart(history).mark_rule(color=INK_MUTED).encode(x=x).transform_filter(hover)
    dots = (
        alt.Chart(history)
        .mark_point(filled=True, size=50, color=INK, stroke=SURFACE, strokeWidth=1.5)
        .encode(x=x, y=y)
        .transform_filter(hover)
    )
    layers = [others, line, name, rule, dots, targets]
    if (history[field] < 0).any():
        zero = (
            alt.Chart(pd.DataFrame({"y": [0]}))
            .mark_rule(color=INK_MUTED, strokeDash=[4, 3])
            .encode(y="y:Q")
        )
        layers.insert(0, zero)
    return alt.layer(*layers).properties(height=300)
