"""Generation mix for display: ENTSO-E production types folded into five groups.

The brand allows at most five categorical colours, and Spain reports around
fifteen production types, so the chart shows groups. Each group keeps the same
colour whatever the date range: colour follows the entity, never its rank, so
changing the dates never repaints a surviving series.

The stack order was chosen with the palette validator (OKLab ΔE under
protanopia and deuteranopia, plus unsimulated vision): every pair of groups
that touch in the stack is told apart. Rust and amber, the brand's closest
pair, never touch. Rust, the accent, marks fossil generation, the technology
that most often sets the price.

Phase 1 replaces this display grouping with a technology-mapping seed in dbt.
"""

import pandas as pd
from theme import AMBER_500, PETROL_900, RUST_500, TEAL_500

TEAL_200 = "#7FB3A4"  # portfolio --teal-200

# Bottom of the stack first. Baseload at the base, weather-driven sources on top.
GROUPS: list[tuple[str, str]] = [
    ("Nuclear", PETROL_900),
    ("Fossil", RUST_500),
    ("Hydro & other", TEAL_200),
    ("Solar", AMBER_500),
    ("Wind", TEAL_500),
]
GROUP_ORDER = [name for name, _ in GROUPS]
GROUP_COLORS = [color for _, color in GROUPS]

_GROUP_OF_PSR = {
    "B14": "Nuclear",
    **dict.fromkeys(["B02", "B03", "B04", "B05", "B06", "B07", "B08"], "Fossil"),
    "B16": "Solar",
    **dict.fromkeys(["B18", "B19"], "Wind"),
}
DEFAULT_GROUP = "Hydro & other"  # hydro, biomass, waste, geothermal, marine, other


def group_of(psr_type: str) -> str:
    return _GROUP_OF_PSR.get(psr_type, DEFAULT_GROUP)


def grouped_mix(generation: pd.DataFrame) -> pd.DataFrame:
    """Hourly generation per group, long format: time, group, generation_mw, share.

    Types are summed within each UTC hour, then hours sharing a local `time`
    are averaged: on the October DST change 02:00 happens twice, and summing
    the two hours drew a spike of double the output.
    """
    if generation.empty:
        return pd.DataFrame(columns=["time", "group", "generation_mw", "share"])
    grouped = (
        generation.assign(group=generation["psr_type"].map(group_of))
        .groupby(["hour_start_utc", "time", "group"], as_index=False)["generation_mw"]
        .sum()
        .groupby(["time", "group"], as_index=False)["generation_mw"]
        .mean()
    )
    total = grouped.groupby("time")["generation_mw"].transform("sum")
    grouped["share"] = grouped["generation_mw"] / total
    grouped["group"] = pd.Categorical(grouped["group"], categories=GROUP_ORDER, ordered=True)
    return grouped.sort_values(["time", "group"]).reset_index(drop=True)


def shares(mix: pd.DataFrame) -> pd.Series:
    """Each group's share of all generation over the period, largest first."""
    totals = mix.groupby("group", observed=True)["generation_mw"].sum()
    return (totals / totals.sum()).sort_values(ascending=False)
