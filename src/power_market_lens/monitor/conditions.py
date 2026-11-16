"""How unusual was each hour of a zone's power system, and why? (ADR-008)

Multivariate statistical process control, per zone and hour, on nine hourly
variables from the marts: load, the load forecast error, generation by group
(wind, solar, nuclear, fossil, hydro & other), net imports and the day-ahead
price.

1. **The calendar is removed with trailing statistics.** Each value becomes a
   z-score against the previous 28 days at the same local hour, from the past
   only. The std has a floor (10% of the variable's std over the reference
   period), so a near-constant cell such as solar at night cannot explode.
2. **PCA on the reference period** (2024), keeping the components that
   explain 80% of the variance.
3. **Two statistics per hour.** Hotelling T² measures how far the hour lies
   from normal inside the usual correlation structure. Q, the squared
   prediction error, measures how far it breaks that structure. The control
   limits are their 99th percentiles over the reference period: empirical,
   because the z-scores are neither normal nor independent (heavy tails,
   autocorrelation), so the textbook F and chi-square limits would not hold.
4. **Each hour is explained** by each variable's contribution to whichever
   statistic lies further beyond its limit. T² contributions add up to T²
   and can be negative; Q contributions are squared residuals and add up to Q.

The result says "statistically unusual for this zone, given the last four
weeks". It is not a blackout probability, a reliability or a security
assessment: ADR-008 measured that these data would not have flagged the hours
before the Iberian blackout of 28 April 2025.

numpy and pandas only. PCA is one SVD.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Column in the feature frame → what the app calls it.
FEATURES: dict[str, str] = {
    "actual_load_mw": "Load",
    "forecast_error_mw": "Load forecast error",
    "wind_mw": "Wind",
    "solar_mw": "Solar",
    "nuclear_mw": "Nuclear",
    "fossil_mw": "Fossil",
    "hydro_other_mw": "Hydro & other",
    "net_import_mw": "Net imports",
    "price_eur_mwh": "Day-ahead price",
}
WINDOW_DAYS = 28
STD_FLOOR = 0.10  # of the variable's std over the reference period
EXPLAINED = 0.80
LIMIT_QUANTILE = 0.99
REFERENCE_END = pd.Timestamp("2025-01-01", tz="UTC")
# A variable present in fewer hours than this share is left out for the zone,
# rather than dropping every hour it is missing from.
MIN_PRESENT = 0.5
# A zone needs this much reference history for the PCA to mean anything.
MIN_REFERENCE_HOURS = 24 * 90
TOP_DRIVERS = 3
KEYS = ["zone_code", "hour_start_utc", "delivery_date_local", "delivery_hour_local"]


@dataclass(frozen=True)
class Model:
    """A zone's fitted PCA and control limits."""

    features: list[str]
    centre: np.ndarray  # mean z over the reference hours
    loadings: np.ndarray  # features × components
    eigenvalues: np.ndarray  # variance of each kept component
    t2_limit: float
    q_limit: float

    @property
    def n_components(self) -> int:
        return self.loadings.shape[1]


def present_features(frame: pd.DataFrame) -> list[str]:
    """The variables this zone reports in most hours (Italy has no nuclear)."""
    return [f for f in FEATURES if f in frame and frame[f].notna().mean() >= MIN_PRESENT]


def trailing_z(frame: pd.DataFrame, features: list[str], reference: np.ndarray) -> pd.DataFrame:
    """z-scores against the previous WINDOW_DAYS days at the same local hour.

    `frame` holds one zone, sorted by time. Only the past enters each mean and
    std (`shift(1)`), so an hour is never compared with itself or the future.
    The first WINDOW_DAYS days of each local hour have no z-score.
    """
    by_hour = frame.groupby("delivery_hour_local")[features]

    def past(statistic: str):
        def rolled(series: pd.Series) -> pd.Series:
            window = series.shift(1).rolling(WINDOW_DAYS, min_periods=WINDOW_DAYS)
            return getattr(window, statistic)()

        return rolled

    floor = frame.loc[reference, features].std() * STD_FLOOR
    std = by_hour.transform(past("std")).clip(lower=floor, axis=1)
    return ((frame[features] - by_hour.transform(past("mean"))) / std).astype(float)


def fit(z_reference: np.ndarray, features: list[str]) -> Model:
    """PCA on the reference z-scores, and the 99th-percentile control limits."""
    centre = z_reference.mean(axis=0)
    x = z_reference - centre
    _, singular, vt = np.linalg.svd(x, full_matrices=False)
    eigenvalues = singular**2 / (len(x) - 1)
    kept = int(np.searchsorted(np.cumsum(eigenvalues) / eigenvalues.sum(), EXPLAINED) + 1)
    model = Model(
        features=features,
        centre=centre,
        loadings=vt[:kept].T,
        eigenvalues=eigenvalues[:kept],
        t2_limit=np.inf,
        q_limit=np.inf,
    )
    t2, q, _, _ = score(model, z_reference)
    return Model(
        features=features,
        centre=centre,
        loadings=model.loadings,
        eigenvalues=model.eigenvalues,
        t2_limit=float(np.quantile(t2, LIMIT_QUANTILE)),
        q_limit=float(np.quantile(q, LIMIT_QUANTILE)),
    )


def score(model: Model, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """T², Q and each variable's contribution to both, one row per hour.

    With x the centred z-scores, scores t = xP and λ the kept eigenvalues:
    T² = Σₐ tₐ²/λₐ, and variable j contributes xⱼ Σₐ (tₐ/λₐ) Pⱼₐ, which adds
    up to T² over j. The residual is e = x − tPᵀ; Q = Σⱼ eⱼ², and each eⱼ² is
    variable j's contribution to Q.
    """
    x = z - model.centre
    scores = x @ model.loadings
    weighted = scores / model.eigenvalues
    t2 = (scores * weighted).sum(axis=1)
    t2_contributions = x * (weighted @ model.loadings.T)
    residual = x - scores @ model.loadings.T
    q_contributions = residual**2
    return t2, q_contributions.sum(axis=1), t2_contributions, q_contributions


def assess_zone(frame: pd.DataFrame) -> pd.DataFrame:
    """One zone's hours, scored against its own reference period.

    `frame` has KEYS, `utc_offset_minutes` and the FEATURES columns. Returns
    one row per hour with a z-score for every variable, from the 29th day of
    history on; empty when the zone has too little reference history.
    """
    frame = frame.sort_values("hour_start_utc").reset_index(drop=True)
    features = present_features(frame)
    frame = frame.dropna(subset=features).reset_index(drop=True)
    reference = (frame["hour_start_utc"] < REFERENCE_END).to_numpy()
    z = trailing_z(frame, features, reference)
    usable = z.notna().all(axis=1).to_numpy()
    frame, z, reference = frame[usable].reset_index(drop=True), z[usable], reference[usable]
    if reference.sum() < MIN_REFERENCE_HOURS:
        return pd.DataFrame()

    values = z.to_numpy()
    model = fit(values[reference], features)
    t2, q, t2_contributions, q_contributions = score(model, values)
    t2_ratio, q_ratio = t2 / model.t2_limit, q / model.q_limit
    by_q = q_ratio > t2_ratio
    # Explain each hour by the statistic that lies further beyond its limit.
    contributions = np.where(by_q[:, None], q_contributions, t2_contributions)
    totals = np.where(by_q, q, t2)
    top = np.argsort(-contributions, axis=1)[:, :TOP_DRIVERS]
    rows = np.arange(len(frame))[:, None]

    result = frame[[*KEYS, "utc_offset_minutes"]].assign(
        is_reference=reference,
        t2=t2,
        q=q,
        t2_limit=model.t2_limit,
        q_limit=model.q_limit,
        unusualness=np.maximum(t2_ratio, q_ratio),
        is_unusual=(t2_ratio > 1) | (q_ratio > 1),
        statistic=np.where(by_q, "Q", "T2"),
        n_components=model.n_components,
        n_features=len(features),
    )
    names = np.array(features)
    for rank in range(TOP_DRIVERS):
        column = top[:, rank]
        result[f"driver_{rank + 1}"] = names[column]
        result[f"driver_{rank + 1}_z"] = values[rows[:, 0], column]
        result[f"driver_{rank + 1}_share"] = contributions[rows[:, 0], column] / totals
    return result


def assess(features: pd.DataFrame) -> pd.DataFrame:
    """Every zone in `features`, each against its own reference period."""
    parts = [assess_zone(zone) for _, zone in features.groupby("zone_code", sort=True)]
    parts = [part for part in parts if not part.empty]
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)
