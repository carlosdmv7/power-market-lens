"""Households page: what a household pays per kWh in each country, and what it pays for.

Two Eurostat datasets (ADR-010): half-yearly prices split by taxes, and yearly
components. They are compiled differently and do not add up to each other,
so they are shown in separate charts and never combined in one figure.
"""

import charts
import data
import streamlit as st
import theme

REPO_URL = "https://github.com/carlosdmv7/power-market-lens"
DEFAULT_BAND = "DC"  # Eurostat's typical household, 2,500 to 4,999 kWh a year
DEFAULT_COUNTRY = "Spain"
BAND_ORDER = ["DA", "DB", "DC", "DD", "DE", "ALL"]
EUR_KWH = "{:,.3f} €/kWh"

TITLE = "What households pay for electricity"
SUBTITLE = (
    "The price a household pays per kWh in each country, with taxes and VAT, and what it "
    "pays for. Source: Eurostat (nrg_pc_204 half-yearly, nrg_pc_204_c yearly components)."
)
DUTCH_REFUND = (
    "In the Netherlands every connection gets a fixed energy-tax refund each year, "
    "whatever it uses."
)


def half_year(period_code: str) -> str:
    year, half = period_code.split("-")
    return f"{'Jan–Jun' if half == 'S1' else 'Jul–Dec'} {year}"


def in_country(name: str) -> str:
    return f"in the {name}" if name == "Netherlands" else f"in {name}"


def band_name(code: str, labels: dict[str, str]) -> str:
    label = labels[code]
    return f"{label} (typical)" if code == DEFAULT_BAND else label


prices = data.household_prices()
if prices.empty:
    theme.render_header(TITLE, SUBTITLE)
    st.info(
        "Household prices are not in the warehouse yet. The next pipeline run will fill this page."
    )
    theme.render_footer(repo_url=REPO_URL)
    st.stop()

header = st.container()

labels = dict(zip(prices["band_code"], prices["band_label"], strict=False))
bands = [code for code in BAND_ORDER if code in labels]
# The band lives in the URL (?band=DA), so a link opens the same view.
requested = st.query_params.get("band")
initial = requested if requested in bands else DEFAULT_BAND if DEFAULT_BAND in bands else bands[0]
band_column, _ = st.columns([1, 2])
band = band_column.selectbox(
    "Yearly consumption",
    bands,
    index=bands.index(initial),
    format_func=lambda code: band_name(code, labels),
    help="Eurostat's household consumption bands. Small users pay more per kWh: "
    "fixed charges are spread over fewer kWh.",
)
st.query_params["band"] = band

in_band = prices[prices["band_code"] == band]
period = in_band["period_code"].max()
latest = in_band[in_band["period_code"] == period].sort_values(
    "price_incl_all_taxes_eur_kwh", ascending=False
)
components = data.household_price_components()
if not components.empty:
    components = components[components["band_code"] == band]

with header:
    theme.render_header(
        TITLE,
        SUBTITLE,
        facts=[
            theme.StripItem(
                "Latest half-year",
                half_year(period),
                help="Eurostat publishes each half-year about four months after it ends.",
            ),
            theme.StripItem(
                "Eurostat version",
                f"{prices['source_updated_at'].max():%d %b %Y}",
                help="When Eurostat last updated the dataset; the pipeline checks daily.",
            ),
            theme.StripItem("Countries", f"{latest['country_code'].nunique()}"),
        ],
    )

# ── The answer first ──────────────────────────────────────────────────────────
dearest, cheapest = latest.iloc[0], latest.iloc[-1]
answer = (
    f"In {half_year(period)}, a household using {labels[band][0].lower()}{labels[band][1:]} "
    "paid between "
    f"{EUR_KWH.format(cheapest['price_incl_all_taxes_eur_kwh'])} {in_country(cheapest['country_name'])} "
    f"and {EUR_KWH.format(dearest['price_incl_all_taxes_eur_kwh'])} {in_country(dearest['country_name'])}, "
    "taxes and VAT included."
)
details = []
spain = latest[latest["country_name"] == DEFAULT_COUNTRY]
if not spain.empty:
    row = spain.iloc[0]
    taxes = row["taxes_and_levies_eur_kwh"] + row["vat_eur_kwh"]
    details.append(
        f"Spain: {EUR_KWH.format(row['price_incl_all_taxes_eur_kwh'])}, of which "
        f"{EUR_KWH.format(taxes)} taxes, levies and VAT."
    )
dutch = latest[latest["country_code"] == "NL"]
if not dutch.empty and dutch.iloc[0]["price_incl_all_taxes_eur_kwh"] < 0:
    details.append(
        f"The Dutch price is below zero. {DUTCH_REFUND} A household using this little gets "
        "back more than its whole bill."
    )
elif not dutch.empty and dutch.iloc[0]["taxes_and_levies_eur_kwh"] < 0:
    details.append(
        f"Dutch taxes and levies are below zero. {DUTCH_REFUND} At this use the refund is "
        "larger than the energy tax charged."
    )
theme.lede(answer, " ".join(details))

# ── The price, split by taxes ─────────────────────────────────────────────────
theme.section(f"Price per kWh by country, {half_year(period)}")
split = latest[["country_name"] + [column for column, _, _ in charts.SPLIT_PARTS]].assign(
    total=latest["price_incl_all_taxes_eur_kwh"]
)
theme.altair_chart(charts.part_bars(split, charts.SPLIT_PARTS, total_title="Price, all taxes"))
st.caption(
    "Before taxes is energy, supply and network costs. The dark tick and the number mark "
    "what the household pays per kWh; where taxes and levies are negative, it sits inside the bar."
)
missing = sorted(set(prices["country_name"]) - set(latest["country_name"]))
if missing:
    st.caption(f"No figure for this half-year yet from: {', '.join(missing)}.")

# ── Over time ─────────────────────────────────────────────────────────────────
history = in_band[
    ["country_name", "period_code", "period_start_date", "price_incl_all_taxes_eur_kwh"]
]
first_year, last_year = (
    history["period_start_date"].min().year,
    history["period_start_date"].max().year,
)
theme.section(f"All taxes included, {first_year} to {last_year}")
countries = sorted(history["country_name"].unique())
country_column, _ = st.columns([1, 2])
highlight = country_column.selectbox(
    "Country to highlight",
    countries,
    index=countries.index(DEFAULT_COUNTRY) if DEFAULT_COUNTRY in countries else 0,
)
theme.altair_chart(charts.price_history_chart(history, highlight))
caption = (
    "One point per half-year, at its first day. Hover to read every country's price "
    "at that half-year."
)
below_zero = history[history["price_incl_all_taxes_eur_kwh"] < 0]
if not below_zero.empty:
    lowest = below_zero.loc[below_zero["price_incl_all_taxes_eur_kwh"].idxmin()]
    caption += (
        f" Below zero: {', '.join(sorted(below_zero['country_name'].unique()))}, as low as "
        f"{EUR_KWH.format(lowest['price_incl_all_taxes_eur_kwh'])} in "
        f"{half_year(lowest['period_code'])}."
    )
    if "NL" in set(in_band.loc[below_zero.index, "country_code"]):
        caption += f" {DUTCH_REFUND} A household using this little gets back more than it pays."
st.caption(caption)

# ── What the price is made of ─────────────────────────────────────────────────
if not components.empty:
    year = int(components["year"].max())
    theme.section(f"What the price is made of, {year}")
    wide = (
        components[components["year"] == year]
        .pivot_table(index="country_name", columns="component", values="component_eur_kwh")
        .reindex(columns=[column for column, _, _ in charts.COMPONENT_PARTS])
        .dropna()
        .reset_index()
    )
    wide["total"] = wide[[column for column, _, _ in charts.COMPONENT_PARTS]].sum(axis=1)
    wide.columns.name = None
    theme.altair_chart(
        charts.part_bars(wide, charts.COMPONENT_PARTS, total_title="Sum of the four")
    )
    st.caption(
        "Eurostat's yearly breakdown of the price into four components. It is compiled "
        "separately from the half-yearly prices above, with its own method, so its totals "
        "differ from them: compare the shares, not the totals across the two charts."
    )

with st.expander("Data table", icon=":material/table:"):
    table = latest[
        [
            "country_name",
            "price_excl_taxes_eur_kwh",
            "taxes_and_levies_eur_kwh",
            "vat_eur_kwh",
            "price_incl_all_taxes_eur_kwh",
        ]
    ].rename(
        columns={
            "country_name": "Country",
            "price_excl_taxes_eur_kwh": "Before taxes (€/kWh)",
            "taxes_and_levies_eur_kwh": "Taxes and levies (€/kWh)",
            "vat_eur_kwh": "VAT (€/kWh)",
            "price_incl_all_taxes_eur_kwh": "Price, all taxes (€/kWh)",
        }
    )
    st.markdown(f":small[{half_year(period)}]")
    st.dataframe(table, hide_index=True, width="stretch")
    if not components.empty:
        shares = (
            components[components["year"] == year]
            .pivot_table(index="country_name", columns="component", values="share_of_total")
            .reindex(columns=[column for column, _, _ in charts.COMPONENT_PARTS])
            .rename(
                columns={column: f"{label} (share)" for column, label, _ in charts.COMPONENT_PARTS}
            )
            .reset_index()
            .rename(columns={"country_name": "Country"})
        )
        st.markdown(f":small[Components, {year}]")
        st.dataframe(
            shares,
            hide_index=True,
            width="stretch",
            column_config={
                column: st.column_config.NumberColumn(format="percent")
                for column in shares.columns
                if column != "Country"
            },
        )

theme.render_footer(repo_url=REPO_URL)
