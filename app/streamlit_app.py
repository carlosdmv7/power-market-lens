"""Power Market Lens: hourly day-ahead price and generation mix per bidding zone.

    streamlit run app/streamlit_app.py

The look comes from the portfolio design system through two native mechanisms
only: `.streamlit/config.toml` theme keys and the Altair theme registered by
`theme.py` (shared byte-for-byte with my other apps). No CSS injection.
"""

from datetime import UTC, datetime, timedelta
import importlib
from pathlib import Path

import hot_reload

# Before anything imports a helper: a Streamlit Cloud redeploy pulls new code
# into the running server, and without this the page would run against the old
# charts/mix/data/theme still cached in sys.modules (see app/hot_reload.py).
# The reloader is itself an app module a deploy can change, so reload it first.
importlib.reload(hot_reload)
hot_reload.drop_stale(Path(__file__).parent)

import charts  # noqa: E402
import data  # noqa: E402
import mix  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import theme  # noqa: E402

DEFAULT_ZONE = "ES"
DEFAULT_DAYS = 7
REPO_URL = "https://github.com/carlosdmv7/power-market-lens"
STALE_AFTER = timedelta(hours=36)  # same threshold as the dbt source freshness error

st.set_page_config(
    page_title="Power Market Lens",
    page_icon=str(Path(__file__).parent / "assets" / "favicon.png"),
    layout="wide",
)


def to_local(frame: pd.DataFrame) -> pd.Series:
    # Charts get naive local wall-clock time, from the offset the mart already
    # derived for each hour from the zone's time zone. On the October DST
    # change the repeated hour 02:00 appears twice; the key stays UTC.
    utc = frame["hour_start_utc"].dt.tz_convert("UTC").dt.tz_localize(None)
    return utc + pd.to_timedelta(frame["utc_offset_minutes"], unit="min")


def hour_label(row: pd.Series) -> str:
    return f"{row['delivery_date_local']:%a %d %b}, {int(row['delivery_hour_local']):02d}:00"


TITLE = "Day-ahead price and generation mix"
SUBTITLE = (
    "What electricity cost on the day-ahead market each hour, and what produced it, "
    "per bidding zone. Source: ENTSO-E Transparency Platform; times are the zone's local time."
)

zones = data.zone_codes()
if not zones:
    theme.render_header(TITLE, SUBTITLE)
    st.info("No data in the warehouse yet. The first pipeline run will fill this page.")
    theme.render_footer(repo_url=REPO_URL)
    st.stop()

# The header shows facts about the zone picked below it, so it is filled last.
header = st.container()

# The zone lives in the URL (?zone=FR), so a link opens the same view.
requested = st.query_params.get("zone")
initial = next((code for code in (requested, DEFAULT_ZONE) if code in zones), zones[0])
zone_column, dates_column = st.columns([1, 2])
zone = zone_column.selectbox(
    "Bidding zone",
    zones,
    index=zones.index(initial),
    help="ENTSO-E bidding zones: Italy has seven, Germany and Luxembourg share one.",
)
st.query_params["zone"] = zone

first_day, last_day, complete_day = data.date_bounds(zone)
latest = data.hourly_prices(zone, last_day, last_day)
loaded_at = latest["loaded_at"].max()
stale = datetime.now(UTC) - loaded_at > STALE_AFTER
with header:
    theme.render_header(
        f"{zone}: {TITLE[0].lower()}{TITLE[1:]}",
        SUBTITLE,
        facts=[
            theme.StripItem(
                "Complete through",
                f"{complete_day:%d %b %Y}",
                help="The last local day with a price for every hour.",
            ),
            theme.StripItem(
                "Last loaded",
                f"{loaded_at:%d %b, %H:%M} UTC",
                tone="warn" if stale else "good",
                help="The pipeline runs daily after the day-ahead auction results are published.",
            ),
            theme.StripItem("Zones", f"{len(zones)} in the warehouse"),
        ],
    )

default_start = max(first_day, last_day - timedelta(days=DEFAULT_DAYS - 1))
picked = dates_column.date_input(
    "Delivery dates",
    value=(default_start, last_day),
    min_value=first_day,
    max_value=last_day,
)
if complete_day < last_day:
    dates_column.caption(
        f"{last_day:%d %b} is one hour short: prices are set per day in Brussels time, "
        f"so {zone}'s last local hour arrives with the next auction."
    )
if not isinstance(picked, tuple) or len(picked) != 2:
    st.stop()  # the user is midway through picking a range
start, end = picked

prices = data.hourly_prices(zone, start, end)
if prices.empty:
    st.info("No prices for these dates.")
    st.stop()
prices = prices.assign(time=to_local(prices))
time_domain = (prices["time"].min(), prices["time"].max())
generation = data.hourly_generation(zone, start, end)
grouped = mix.grouped_mix(generation.assign(time=to_local(generation)))

# ── The answer first ──────────────────────────────────────────────────────────
cheapest = prices.loc[prices["price_eur_mwh"].idxmin()]
dearest = prices.loc[prices["price_eur_mwh"].idxmax()]
period = f"{start:%d %b}" if start == end else f"{start:%d %b} to {end:%d %b}"
detail = ""
if not grouped.empty:
    top = mix.shares(grouped)
    detail = f"Largest source: {top.index[0].lower()}, {top.iloc[0]:.0%} of generation."
theme.lede(
    f"From {period}, the day-ahead price averaged {prices['price_eur_mwh'].mean():,.2f} €/MWh, "
    f"between {cheapest['price_eur_mwh']:,.2f} and {dearest['price_eur_mwh']:,.2f} €/MWh.",
    detail,
)

avg, low, high, negative = st.columns(4)
avg.metric("Average price", f"{prices['price_eur_mwh'].mean():,.2f} €/MWh")
low.metric("Cheapest hour", f"{cheapest['price_eur_mwh']:,.2f} €/MWh")
low.caption(hour_label(cheapest))
high.metric("Most expensive hour", f"{dearest['price_eur_mwh']:,.2f} €/MWh")
high.caption(hour_label(dearest))
negative.metric("Hours at or below zero", int((prices["price_eur_mwh"] <= 0).sum()))

# ── Price ─────────────────────────────────────────────────────────────────────
theme.section("Hourly day-ahead price")
theme.altair_chart(charts.price_chart(prices, time_domain))
st.caption(
    "Since 1 October 2025 the market clears every 15 minutes; the hourly price shown "
    "is the average of the hour's four quarter-hour prices."
)
if prices["has_price_out_of_range"].any():
    st.warning(
        "Some prices fall outside the SDAC harmonised limits in force on their day. "
        "They are shown as published and flagged in the warehouse.",
        icon=":material/flag:",
    )

# ── Generation ────────────────────────────────────────────────────────────────
theme.section("Generation by technology")
if grouped.empty:
    st.info("No generation data for these dates.")
else:
    theme.altair_chart(charts.mix_chart(grouped, time_domain))
    caption = (
        "ENTSO-E's production types are grouped into five: hydro, biomass, waste and the "
        "remaining sources are shown together as hydro & other."
    )
    latest_generation = grouped["time"].max()
    if latest_generation < time_domain[1]:
        # Prices are set a day ahead; generation is reported as it happens.
        caption += (
            " Generation is reported after the fact, so it runs only to "
            f"{latest_generation:%a %d %b, %H:%M}; the prices go further ahead."
        )
    st.caption(caption)

with st.expander("Data table", icon=":material/table:"):
    table = prices[["time", "price_eur_mwh"]].rename(
        columns={"time": "Hour (local)", "price_eur_mwh": "Price (€/MWh)"}
    )
    if not grouped.empty:
        wide = grouped.pivot_table(
            index="time", columns="group", values="generation_mw", observed=True
        ).add_suffix(" (MW)")
        table = table.merge(wide, left_on="Hour (local)", right_index=True, how="left")
    st.dataframe(table, hide_index=True, width="stretch")

theme.render_footer(repo_url=REPO_URL)
