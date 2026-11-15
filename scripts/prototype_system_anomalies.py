"""Prototype for ADR-008: how unusual is each hour of a zone's power system?

    uv run --env-file .env python scripts/prototype_system_anomalies.py --zone ES

Read-only on the warehouse (WAREHOUSE_PATH, default md:power_prod). Not part
of the pipeline or the app: it exists so that the numbers in ADR-008 can be
reproduced and challenged.

Method (multivariate statistical process control):

1. Nine hourly variables per zone from the marts: load, the load forecast
   error, generation by group (wind, solar, nuclear, fossil, hydro & other),
   net imports over all borders, and the day-ahead price. A zone without a
   technology (no nuclear in Italy) has that column dropped.
2. The calendar is removed with trailing statistics: each value becomes a z-score
   against the previous 28 days at the same local hour, using only the past.
   The std has a floor (10% of the variable's overall std) so that
   near-constant cells, such as solar at night, cannot blow up.
3. PCA is fitted on the reference year (2024) and keeps the components that
   explain 80% of the variance.
4. Two statistics per hour: Hotelling T² (distance from normal inside the
   usual correlation structure) and Q, or SPE (how far the hour breaks that
   structure). Control limits are the reference year's 99th percentiles.
5. Each flagged hour is explained by each variable's contribution to T².
"""

import argparse
import os

import duckdb
import numpy as np
import pandas as pd

FEATURES = [
    "actual_load_mw",
    "forecast_error_mw",
    "wind_mw",
    "solar_mw",
    "nuclear_mw",
    "fossil_mw",
    "hydro_other_mw",
    "net_import_mw",
    "price_eur_mwh",
]
WINDOW_DAYS = 28
STD_FLOOR = 0.10
EXPLAINED = 0.80
LIMIT_QUANTILE = 0.99
REFERENCE_END = pd.Timestamp("2025-01-01", tz="UTC")
BLACKOUT_DAY = pd.Timestamp("2025-04-28")

QUERY = """
with load as (
    select hour_start_utc, delivery_date_local, delivery_hour_local,
           actual_load_mw, forecast_error_mw
    from marts.fct_load_hourly where zone_code = $zone
),
gen as (
    select hour_start_utc,
           sum(generation_mw) filter (where display_group = 'Wind') as wind_mw,
           sum(generation_mw) filter (where display_group = 'Solar') as solar_mw,
           sum(generation_mw) filter (where display_group = 'Nuclear') as nuclear_mw,
           sum(generation_mw) filter (where display_group = 'Fossil') as fossil_mw,
           sum(generation_mw) filter (where display_group = 'Hydro & other') as hydro_other_mw
    from marts.fct_generation_hourly where zone_code = $zone
    group by 1
),
flows as (
    select hour_start_utc,
           sum(net_import_mw) as net_import_mw
    from marts.fct_interchange_hourly where zone_code = $zone
    group by 1
),
price as (
    select hour_start_utc, price_eur_mwh
    from marts.fct_day_ahead_price_hourly where zone_code = $zone
)
select * from load
join gen using (hour_start_utc)
join flows using (hour_start_utc)
join price using (hour_start_utc)
order by hour_start_utc
"""


def load_frame(zone: str) -> pd.DataFrame:
    path = os.getenv("WAREHOUSE_PATH", "md:power_prod")
    config = {"TimeZone": "UTC"}
    if path.startswith("md:"):
        config["motherduck_token"] = os.environ["MOTHERDUCK_TOKEN"]
    with duckdb.connect(path, read_only=not path.startswith("md:"), config=config) as con:
        frame = con.execute(QUERY, {"zone": zone}).df()
    # A technology the zone does not have (nuclear in Italy) is all null: drop it.
    present = [f for f in FEATURES if frame[f].notna().any()]
    frame = frame.dropna(subset=present).sort_values("hour_start_utc").reset_index(drop=True)
    frame.attrs["features"] = present
    frame["date"] = pd.to_datetime(frame["delivery_date_local"])
    return frame


def trailing_z(frame: pd.DataFrame, reference: np.ndarray) -> pd.DataFrame:
    """z-scores against the previous WINDOW_DAYS days at the same local hour."""
    features = frame.attrs["features"]
    by_hour = frame.groupby("delivery_hour_local")[features]

    def past_mean(series: pd.Series) -> pd.Series:
        return series.shift(1).rolling(WINDOW_DAYS, min_periods=WINDOW_DAYS).mean()

    def past_std(series: pd.Series) -> pd.Series:
        return series.shift(1).rolling(WINDOW_DAYS, min_periods=WINDOW_DAYS).std()

    floor = frame.loc[reference, features].std() * STD_FLOOR
    std = by_hour.transform(past_std).clip(lower=floor, axis=1)
    return ((frame[features] - by_hour.transform(past_mean)) / std).astype(float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zone", default="ES")
    zone = parser.parse_args().zone

    frame = load_frame(zone)
    features = frame.attrs["features"]
    reference = (frame["hour_start_utc"] < REFERENCE_END).to_numpy()
    z = trailing_z(frame, reference)
    usable = z.notna().all(axis=1).to_numpy()
    frame, z, reference = frame[usable].reset_index(drop=True), z[usable], reference[usable]
    print(f"{zone}: {reference.sum()} reference hours, {(~reference).sum()} evaluation hours")

    centre = z[reference].to_numpy().mean(axis=0)
    x = z.to_numpy() - centre
    _, singular, vt = np.linalg.svd(x[reference], full_matrices=False)
    eigen = singular**2 / (reference.sum() - 1)
    k = int(np.searchsorted(np.cumsum(eigen) / eigen.sum(), EXPLAINED) + 1)
    loadings = vt[:k].T
    scores = x @ loadings
    t2 = (scores**2 / eigen[:k]).sum(axis=1)
    q = ((x - scores @ loadings.T) ** 2).sum(axis=1)
    t2_limit = np.quantile(t2[reference], LIMIT_QUANTILE)
    q_limit = np.quantile(q[reference], LIMIT_QUANTILE)
    frame = frame.assign(t2=t2, q=q, flag=(t2 > t2_limit) | (q > q_limit))
    print(f"{k} components of {len(features)}; limits T² {t2_limit:.1f}, Q {q_limit:.1f}")
    print(
        f"hours flagged: reference {frame.loc[reference, 'flag'].mean():.1%}, "
        f"evaluation {frame.loc[~reference, 'flag'].mean():.1%}"
    )

    def contributions(row: int) -> pd.Series:
        per_component = (x[row] @ loadings / eigen[:k]) * (loadings * x[row][:, None])
        return pd.Series(per_component.sum(axis=1), index=features).sort_values(ascending=False)

    evaluation = frame[~reference]
    daily = evaluation.groupby("date").agg(
        flagged=("flag", "sum"), hours=("flag", "size"), t2_max=("t2", "max")
    )
    daily["share"] = daily["flagged"] / daily["hours"]
    daily = daily.sort_values(["share", "t2_max"], ascending=False)
    print("\nMost unusual evaluation days, with the top contributors at their highest-T² hour:")
    for day, row in daily.head(8).iterrows():
        peak = int(evaluation.loc[evaluation["date"] == day, "t2"].idxmax())
        top = ", ".join(f"{n} {v:.0f}" for n, v in contributions(peak).head(3).items())
        print(
            f"  {day:%Y-%m-%d}  {row.flagged:2.0f}/{row.hours:.0f} h  T² max {row.t2_max:6.1f}  {top}"
        )

    blackout = frame[frame["date"] == BLACKOUT_DAY]
    if not blackout.empty:
        print(f"\n{BLACKOUT_DAY:%d %b %Y}, the hours published before the blackout:")
        for _, row in blackout.iterrows():
            t2_pct = (t2[reference] < row.t2).mean()
            q_pct = (q[reference] < row.q).mean()
            print(
                f"  {int(row.delivery_hour_local):02d}:00  T² percentile {t2_pct:.2f}  "
                f"Q percentile {q_pct:.2f}  flagged {row.flag}"
            )
        quiet = int((daily["flagged"] == 0).sum())
        print(
            f"  {int(blackout['flag'].sum())} of {len(blackout)} hours flagged; "
            f"{quiet} of {len(daily)} evaluation days have no flagged hour at all"
        )


if __name__ == "__main__":
    main()
