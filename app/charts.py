"""The page's two charts, built on the brand Altair theme registered by theme.py.

The charts are read one above the other, price against mix, so both take the
same time domain: the same hour sits at the same x in each, although actual
generation stops at the latest published hour while prices run into tomorrow.

Both share the same hover: a vertical rule follows the pointer to the nearest
hour, and a tooltip gives the exact values, so no number has to be read off an
axis.
"""

import altair as alt
from mix import GROUP_COLORS, GROUP_ORDER
import pandas as pd
from theme import INK_MUTED, RUST_500, SURFACE

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


def price_chart(prices: pd.DataFrame, domain: tuple[pd.Timestamp, pd.Timestamp]) -> alt.LayerChart:
    """Hourly price, one series. A zero line appears when prices go negative."""
    hover = alt.selection_point(
        fields=["time"], nearest=True, on="pointerover", clear="pointerout", empty=False
    )
    base = alt.Chart(prices).encode(x=time_x(domain, title=None, axis=DAY_AXIS))
    line = base.mark_line(stroke=RUST_500, strokeWidth=2, clip=True).encode(
        y=alt.Y("price_eur_mwh:Q", title="€/MWh", axis=y_axis()),
    )
    targets = (
        base.mark_point(opacity=0, size=200, clip=True)
        .encode(
            y="price_eur_mwh:Q",
            tooltip=[
                alt.Tooltip("time:T", title="Hour", format=TIME_FORMAT),
                alt.Tooltip("price_eur_mwh:Q", title="€/MWh", format=",.2f"),
            ],
        )
        .add_params(hover)
    )
    rule = base.mark_rule(color=INK_MUTED, clip=True).transform_filter(hover)
    dot = line.mark_point(
        filled=True, size=70, color=RUST_500, stroke=SURFACE, strokeWidth=2, clip=True
    )
    dot = dot.transform_filter(hover)

    layers = [line, rule, dot, targets]
    if (prices["price_eur_mwh"] < 0).any():
        zero = (
            alt.Chart(pd.DataFrame({"y": [0]}))
            .mark_rule(color=INK_MUTED, strokeDash=[4, 3])
            .encode(y="y:Q")
        )
        layers.insert(0, zero)
    return alt.layer(*layers).properties(height=280)


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


def price_map(features: list[dict], prices: pd.DataFrame) -> alt.LayerChart:
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
    return alt.layer(land, zones).project(type="mercator").properties(height=MAP_HEIGHT)


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
