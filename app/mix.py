"""Generation mix for display: the five groups of the technology_mapping seed.

The brand allows at most five categorical colours, and a zone reports around
fifteen production types, so the chart shows groups. Which type belongs to
which group is decided in dbt (technology_mapping.display_group, ADR-004) and
arrives with the mart; this module only fixes their order and colours. Each
group keeps the same colour whatever the date range: colour follows the
entity, never its rank, so changing the dates never repaints a surviving series.

The stack order was chosen with the palette validator (OKLab ΔE under
protanopia and deuteranopia, plus unsimulated vision): every pair of groups
that touch in the stack is told apart. Rust and amber, the brand's closest
pair, never touch. Rust, the accent, marks fossil generation, the technology
that most often sets the price.
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


def grouped_mix(generation: pd.DataFrame) -> pd.DataFrame:
    """Hourly generation per group, long format: time, group, generation_mw, share.

    `generation` carries the mart's display_group. A group outside the five
    means the seed changed without this module: fail, rather than draw a
    series in no colour.
    """
    if generation.empty:
        return pd.DataFrame(columns=["time", "group", "generation_mw", "share"])
    unknown = set(generation["display_group"].dropna()) - set(GROUP_ORDER)
    if unknown:
        raise ValueError(f"Display groups without a colour: {sorted(unknown)}")
    grouped = (
        generation.rename(columns={"display_group": "group"})
        .dropna(subset=["group"])
        .groupby(["time", "group"], as_index=False)["generation_mw"]
        .sum()
    )
    total = grouped.groupby("time")["generation_mw"].transform("sum")
    grouped["share"] = grouped["generation_mw"] / total
    grouped["group"] = pd.Categorical(grouped["group"], categories=GROUP_ORDER, ordered=True)
    return grouped.sort_values(["time", "group"]).reset_index(drop=True)


def shares(mix: pd.DataFrame) -> pd.Series:
    """Each group's share of all generation over the period, largest first."""
    totals = mix.groupby("group", observed=True)["generation_mw"].sum()
    return (totals / totals.sum()).sort_values(ascending=False)
