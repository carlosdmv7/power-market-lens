"""Power Market Lens — Spain: hourly day-ahead price and generation mix.

streamlit run app/streamlit_app.py
"""

from datetime import timedelta

import data
import pandas as pd
import streamlit as st

ZONE = "ES"
ZONE_TIMEZONE = "Europe/Madrid"
DEFAULT_DAYS = 7

st.set_page_config(page_title="Power Market Lens", page_icon="⚡", layout="wide")


def to_local(utc: pd.Series) -> pd.Series:
    # Charts get naive Madrid wall-clock time. On the October DST change the
    # repeated hour 02:00 appears twice; the key in the warehouse stays UTC.
    return utc.dt.tz_convert(ZONE_TIMEZONE).dt.tz_localize(None)


st.title("Spain: day-ahead price and generation mix")
st.caption(
    "Hourly day-ahead prices (SDAC) and actual generation by technology, from the "
    "ENTSO-E Transparency Platform. Times are Spanish local time."
)

bounds = data.date_bounds(ZONE)
if bounds is None:
    st.info("No data in the warehouse yet. The first pipeline run will fill this page.")
    st.stop()

first_day, last_day = bounds
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
generation = data.hourly_generation(ZONE, start, end)

if prices.empty:
    st.info("No prices for these dates.")
    st.stop()

cheapest = prices.loc[prices["price_eur_mwh"].idxmin()]
dearest = prices.loc[prices["price_eur_mwh"].idxmax()]
avg, low, high, negative = st.columns(4)
avg.metric("Average price", f"{prices['price_eur_mwh'].mean():.2f} €/MWh")
low.metric("Cheapest hour", f"{cheapest['price_eur_mwh']:.2f} €/MWh")
low.caption(
    f"{cheapest['delivery_date_local']:%d %b}, {int(cheapest['delivery_hour_local']):02d}:00"
)
high.metric("Most expensive hour", f"{dearest['price_eur_mwh']:.2f} €/MWh")
high.caption(
    f"{dearest['delivery_date_local']:%d %b}, {int(dearest['delivery_hour_local']):02d}:00"
)
negative.metric("Hours at or below zero", int((prices["price_eur_mwh"] <= 0).sum()))

st.subheader("Hourly day-ahead price")
st.line_chart(
    prices.assign(time=to_local(prices["hour_start_utc"])),
    x="time",
    y="price_eur_mwh",
    x_label="Local time",
    y_label="€/MWh",
)
st.caption(
    "Since 1 October 2025 the market clears every 15 minutes; the hourly price "
    "shown is the average of the hour's four quarter-hour prices."
)
if prices["has_price_out_of_range"].any():
    st.warning(
        "Some prices fall outside the SDAC harmonised limits in force on their day; "
        "they are shown as published and flagged in the warehouse."
    )

st.subheader("Generation by technology")
if generation.empty:
    st.info("No generation data for these dates.")
else:
    mix = (
        generation.assign(time=to_local(generation["hour_start_utc"]))
        .pivot_table(index="time", columns="psr_name", values="generation_mw", aggfunc="sum")
        .fillna(0)
    )
    # Stacked: the height of the stack is total generation at each hour.
    st.area_chart(mix, x_label="Local time", y_label="MW (average over the hour)", stack=True)

loaded = prices["loaded_at"].max()
st.caption(f"Data last loaded {loaded:%Y-%m-%d %H:%M} UTC.")
