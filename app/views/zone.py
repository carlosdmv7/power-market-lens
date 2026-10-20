"""Zone page: hourly day-ahead price and generation mix for one bidding zone."""

from datetime import UTC, datetime, timedelta

import charts
import data
import mix
import pandas as pd
import streamlit as st
import theme

DEFAULT_ZONE = "ES"
DEFAULT_DAYS = 7
REPO_URL = "https://github.com/carlosdmv7/power-market-lens"
STALE_AFTER = timedelta(hours=36)  # same threshold as the dbt source freshness error


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
metrics = data.hourly_mix_metrics(zone, start, end)
metrics = metrics.assign(time=to_local(metrics))
load = data.hourly_load(zone, start, end)
load = load.assign(time=to_local(load))
measured = load.dropna(subset=["actual_load_mw", "forecast_load_mw"])
# Above this, forecast and actual disagree by more than any forecast misses:
# the two series most likely cover different things (seen in NL, 2026-10).
SYSTEMATIC_BIAS = 0.05
STEADY_SHARE = 0.9  # of hours with the error on the bias's side


def production_weighted(column: str) -> float:
    # A period's share or intensity weights each hour by what it produced:
    # the plain mean of hourly shares would count a quiet night like a peak.
    weights = metrics["production_mw"]
    return (metrics[column] * weights).sum() / weights.sum()


# ── The answer first ──────────────────────────────────────────────────────────
cheapest = prices.loc[prices["price_eur_mwh"].idxmin()]
dearest = prices.loc[prices["price_eur_mwh"].idxmax()]
period = f"{start:%d %b}" if start == end else f"{start:%d %b} to {end:%d %b}"
detail = ""
if not grouped.empty:
    top = mix.shares(grouped)
    detail = f"Largest source: {top.index[0].lower()}, {top.iloc[0]:.0%} of generation."
if not metrics.empty:
    detail += (
        f" Renewables made {production_weighted('renewable_share'):.0%} of it, at "
        f"{production_weighted('carbon_intensity_g_per_kwh'):,.0f} gCO₂eq per kWh."
    )
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
if not metrics.empty:
    renewable, low_carbon, intensity, demand = st.columns(4)
    renewable.metric("Renewable share", f"{production_weighted('renewable_share'):.0%}")
    low_carbon.metric("Low-carbon share", f"{production_weighted('low_carbon_share'):.0%}")
    low_carbon.caption("Renewables and nuclear")
    intensity.metric(
        "Carbon intensity", f"{production_weighted('carbon_intensity_g_per_kwh'):,.0f} g/kWh"
    )
    intensity.caption("gCO₂eq per kWh produced, lifecycle")
    if not measured.empty:
        demand.metric("Average load", f"{measured['actual_load_mw'].mean():,.0f} MW")
        miss = measured["forecast_error_mw"].abs().mean() / measured["actual_load_mw"].mean()
        demand.caption(f"Day-ahead forecast off by {miss:.1%} on average")

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
        "ENTSO-E's production types are grouped into five: hydro, biomass, waste, storage "
        "and the remaining sources are shown together as hydro & other."
    )
    latest_generation = grouped["time"].max()
    if latest_generation < time_domain[1]:
        # Prices are set a day ahead; generation is reported as it happens.
        caption += (
            " Generation is reported after the fact, so it runs only to "
            f"{latest_generation:%a %d %b, %H:%M}; the prices go further ahead."
        )
    st.caption(caption)

# ── Load ──────────────────────────────────────────────────────────────────────
if not load.empty:
    theme.section("Electricity demand")
    theme.altair_chart(charts.load_chart(load, time_domain))
    caption = (
        "Total load as the TSO reports it, against the forecast it published the day "
        "before. The forecast runs into tomorrow; the actual stops at the latest hour reported."
    )
    if not measured.empty:
        bias = measured["forecast_error_mw"].mean() / measured["actual_load_mw"].mean()
        # Large and one-sided: the caption below says "almost every hour", so
        # that is what is checked. A small zone's noisy forecast (IT-CALA) is
        # large but two-sided, and is not called a definition gap.
        same_side = (measured["forecast_error_mw"] * bias > 0).mean()
        if abs(bias) > SYSTEMATIC_BIAS and same_side >= STEADY_SHARE:
            caption += (
                f" Here the forecast sits {abs(bias):.0%} {'above' if bias > 0 else 'below'} "
                "the actual almost every hour: too steady to be forecasting error, so the two "
                "series most likely do not cover the same consumption."
            )
    st.caption(caption)

# ── Emissions ─────────────────────────────────────────────────────────────────
if not metrics.empty:
    theme.section("Carbon intensity of production")
    theme.altair_chart(charts.intensity_chart(metrics, time_domain))
    caption = (
        "Lifecycle emissions per kWh produced in the zone, from IPCC AR5 medians by "
        "technology. Production-based: imported power is not counted, and storage, "
        "which re-delivers power produced earlier, is left out."
    )
    proxy = production_weighted("proxy_factor_share")
    if proxy >= 0.05:
        caption += (
            f" {proxy:.0%} of this zone's production has no AR5 factor of its own and takes "
            "a stated proxy, so the intensity is approximate. Where that is lignite, which "
            "takes hard coal's factor though it emits more, it is understated."
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
    if not metrics.empty:
        emissions = metrics.set_index("time")[
            ["renewable_share", "carbon_intensity_g_per_kwh"]
        ].rename(
            columns={
                "renewable_share": "Renewable share",
                "carbon_intensity_g_per_kwh": "Carbon intensity (gCO₂eq/kWh)",
            }
        )
        table = table.merge(emissions, left_on="Hour (local)", right_index=True, how="left")
    st.dataframe(table, hide_index=True, width="stretch")

theme.render_footer(repo_url=REPO_URL)
