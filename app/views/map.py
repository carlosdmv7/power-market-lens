"""Map page: one delivery day's day-ahead price across the modelled zones."""

from datetime import UTC, datetime, timedelta

import charts
import data
import streamlit as st
import theme

REPO_URL = "https://github.com/carlosdmv7/power-market-lens"
STALE_AFTER = timedelta(hours=36)  # same threshold as the dbt source freshness error

TITLE = "Day-ahead prices across Europe"
SUBTITLE = (
    "The mean day-ahead price of one delivery day in each bidding zone, from Portugal to "
    "southern Italy. Source: ENTSO-E Transparency Platform; each zone's day is its local day."
)

days = data.price_days()
if days is None:
    theme.render_header(TITLE, SUBTITLE)
    st.info("No data in the warehouse yet. The first pipeline run will fill this page.")
    theme.render_footer(repo_url=REPO_URL)
    st.stop()

first_day, last_day, complete_day = days
header = st.container()
day = st.date_input(
    "Delivery day",
    value=complete_day,
    min_value=first_day,
    max_value=last_day,
    help="Defaults to the latest day with every hour priced in every zone.",
)
prices = data.daily_zone_prices(day)
loaded_at = data.hourly_prices(prices["zone_code"].iloc[0], day, day)["loaded_at"].max()
stale = datetime.now(UTC) - loaded_at > STALE_AFTER
with header:
    theme.render_header(
        TITLE,
        SUBTITLE,
        facts=[
            theme.StripItem(
                "Complete in every zone through",
                f"{complete_day:%d %b %Y}",
                help="The last local day with every hour priced in every zone.",
            ),
            theme.StripItem(
                "Last loaded",
                f"{loaded_at:%d %b, %H:%M} UTC",
                tone="warn" if stale else "good",
                help="The pipeline runs daily after the day-ahead auction results are published.",
            ),
            theme.StripItem("Zones", f"{len(prices)} priced on this day"),
        ],
    )

if prices.empty:
    st.info("No prices for this day.")
    st.stop()


# ── The answer first ──────────────────────────────────────────────────────────
def zones_at(price: float) -> str:
    # Coupled zones with free capacity between them clear at the same price,
    # so the top or bottom is often shared (IT-NORD and IT-CNOR): name them all.
    tied = sorted(prices.loc[(prices["price_eur_mwh"] - price).abs() < 0.005, "zone_code"])
    return tied[0] if len(tied) == 1 else ", ".join(tied[:-1]) + " and " + tied[-1]


high, low = prices["price_eur_mwh"].max(), prices["price_eur_mwh"].min()
if high - low < 0.005:
    theme.lede(
        f"On {day:%A %d %B %Y}, every zone cleared at the same price on average: "
        f"{high:,.2f} €/MWh.",
        "No line between them was full for long enough to split the market.",
    )
else:
    theme.lede(
        f"On {day:%A %d %B %Y}, power cost most in {zones_at(high)} "
        f"({high:,.2f} €/MWh on average) and least in {zones_at(low)} ({low:,.2f} €/MWh).",
        f"A spread of {high - low:,.2f} €/MWh: zones split when the lines between them are full.",
    )

map_column, ranking_column = st.columns([3, 2])
with map_column:
    theme.section("Mean price by zone")
    theme.altair_chart(charts.price_map(data.zone_geometry(), prices))
with ranking_column:
    theme.section("Ranked")
    theme.altair_chart(charts.price_ranking(prices))
st.caption(
    "Grey land is outside this app's twelve zones. Each zone's price is the mean of its "
    "local day's hours; since 1 October 2025 each hour is itself the mean of four "
    "quarter-hour prices."
)
short = prices[prices["n_hours"] < prices["hours_in_delivery_day"]]
if not short.empty:
    st.caption(
        f"Not every hour is in yet for {', '.join(short['zone_code'])}: their mean covers "
        "the hours published so far."
    )

with st.expander("Data table", icon=":material/table:"):
    st.dataframe(
        prices[["zone_code", "price_eur_mwh", "min_price_eur_mwh", "max_price_eur_mwh"]].rename(
            columns={
                "zone_code": "Zone",
                "price_eur_mwh": "Mean (€/MWh)",
                "min_price_eur_mwh": "Cheapest hour (€/MWh)",
                "max_price_eur_mwh": "Dearest hour (€/MWh)",
            }
        ),
        hide_index=True,
        width="stretch",
    )

theme.render_footer(repo_url=REPO_URL)
