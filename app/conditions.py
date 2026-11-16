"""Words for the monitor of unusual system conditions (ADR-008).

The pipeline stores, per hour, how unusual it was (`unusualness`, 1 at the
control limit) and its top variables with their z-scores. This module turns
them into what a visitor reads: which variables stood out and which way, and
runs of consecutive unusual hours grouped into one episode, because hours are
autocorrelated and a single event flags several in a row.
"""

import pandas as pd

# The monitor's variable names (power_market_lens.monitor.conditions.FEATURES).
# Repeated here because the app ships without the pipeline's package.
LABELS = {
    "actual_load_mw": "load",
    "forecast_error_mw": "load forecast error",
    "wind_mw": "wind",
    "solar_mw": "solar",
    "nuclear_mw": "nuclear",
    "fossil_mw": "fossil",
    "hydro_other_mw": "hydro & other",
    "net_import_mw": "net imports",
    "price_eur_mwh": "price",
}


def stood_out(row: pd.Series, drivers: int = 2) -> str:
    """'Nuclear far below usual, load forecast error above usual'."""
    parts = []
    for n in range(1, drivers + 1):
        z = row[f"driver_{n}_z"]
        size = "far " if abs(z) >= 5 else ""
        side = "above" if z > 0 else "below"
        parts.append(f"{LABELS.get(row[f'driver_{n}'], row[f'driver_{n}'])} {size}{side} usual")
    text = ", ".join(parts)
    return text[:1].upper() + text[1:]


def episodes(conditions: pd.DataFrame) -> pd.DataFrame:
    """Consecutive unusual hours as one episode, with its peak.

    `conditions` has a local `time` column (naive wall clock) and the
    monitor's columns. Returns one row per episode, latest first: start,
    hours, peak unusualness, and what stood out at the peak.
    """
    unusual = conditions[conditions["is_unusual"]].sort_values("hour_start_utc")
    if unusual.empty:
        return pd.DataFrame(columns=["start", "hours", "peak", "what"])
    gap = unusual["hour_start_utc"].diff() != pd.Timedelta(hours=1)
    episode = gap.cumsum()
    rows = []
    for _, hours in unusual.groupby(episode):
        peak = hours.loc[hours["unusualness"].idxmax()]
        rows.append(
            {
                "start": hours["time"].iloc[0],
                "hours": len(hours),
                "peak": peak["unusualness"],
                "what": stood_out(peak),
            }
        )
    return pd.DataFrame(rows).sort_values("start", ascending=False, ignore_index=True)
