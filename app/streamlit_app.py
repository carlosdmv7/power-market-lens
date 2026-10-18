"""Power Market Lens — Spain: hourly day-ahead price and generation mix.

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

ZONE = "ES"
ZONE_TIMEZONE = "Europe/Madrid"
DEFAULT_DAYS = 7
REPO_URL = "https://github.com/carlosdmv7/power-market-lens"
STALE_AFTER = timedelta(hours=36)  # same threshold as the dbt source freshness error

st.set_page_config(
    page_title="Power Market Lens",
    page_icon=str(Path(__file__).parent / "assets" / "favicon.png"),
    layout="wide",
)


def to_local(utc: pd.Series) -> pd.Series:
    # Charts get naive Madrid wall-clock time. On the October DST change the
    # repeated hour 02:00 appears twice; the key in the warehouse stays UTC.
    return utc.dt.tz_convert(ZONE_TIMEZONE).dt.tz_localize(None)


def hour_label(row: pd.Series) -> str:
    return f"{row['delivery_date_local']:%a %d %b}, {int(row['delivery_hour_local']):02d}:00"


TITLE = "Spain: day-ahead price and generation mix"
SUBTITLE = (
    "What electricity cost on Spain's day-ahead market each hour, and what produced it. "
    "Source: ENTSO-E Transparency Platform; times are Spanish local time."
)

bounds = data.date_bounds(ZONE)
if bounds is None:
    theme.render_header(TITLE, SUBTITLE)
    st.info("No data in the warehouse yet. The first pipeline run will fill this page.")
    theme.render_footer(repo_url=REPO_URL)
    st.stop()

first_day, last_day = bounds
latest = data.hourly_prices(ZONE, last_day, last_day)
loaded_at = latest["loaded_at"].max()
stale = datetime.now(UTC) - loaded_at > STALE_AFTER
theme.render_header(
    TITLE,
    SUBTITLE,
    facts=[
        theme.StripItem("Prices through", f"{last_day:%d %b %Y}"),
        theme.StripItem(
            "Last loaded",
            f"{loaded_at:%d %b, %H:%M} UTC",
            tone="warn" if stale else "good",
            help="The pipeline runs daily after the day-ahead auction results are published.",
        ),
        theme.StripItem("Zone", "ES · 10YES-REE------0"),
    ],
)

default_start = max(first_day, last_day - timedelta(days=DEFAULT_DAYS - 1))
picked = st.date_input(
    "Delivery dates",
    value=(default_start, last_day),
    min_value=first_day,
    max_value=last_day,
)
if not isinstance(picked, tuple) or len(picked) != 2:
    st.stop()  # the user is midway through picking a range
start, end = picked

prices = data.hourly_prices(ZONE, start, end)
if prices.empty:
    st.info("No prices for these dates.")
    st.stop()
prices = prices.assign(time=to_local(prices["hour_start_utc"]))
generation = data.hourly_generation(ZONE, start, end)
grouped = mix.grouped_mix(generation.assign(time=to_local(generation["hour_start_utc"])))

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
theme.altair_chart(charts.price_chart(prices))
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
    theme.altair_chart(charts.mix_chart(grouped))
    st.caption(
        "ENTSO-E's production types are grouped into five: hydro, biomass, waste and the "
        "remaining sources are shown together as hydro & other."
    )

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
