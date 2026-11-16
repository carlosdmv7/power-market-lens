"""Synthetic zones whose normal behaviour is known, shared by the monitor tests."""

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest


def synthetic_zone(code: str = "ES", seed: int = 7, end: str = "2025-06-30 23:00") -> pd.DataFrame:
    """Eighteen months of one zone: a daily cycle, correlated variables, noise.

    Fossil fills what load leaves after the others, and the price follows
    fossil, so the variables move together the way a real system's do.
    """
    rng = np.random.default_rng(seed)
    hours = pd.date_range("2024-01-01", end, freq="h", tz="UTC")
    n = len(hours)
    hour = hours.hour.to_numpy()
    daily = np.sin(2 * np.pi * (hour - 6) / 24)
    load = 30000 + 5000 * daily + rng.normal(0, 500, n)
    solar = np.clip(8000 * daily, 0, None) + rng.normal(0, 200, n) * (daily > 0)
    wind = 6000 + rng.normal(0, 1500, n)
    nuclear = 7000 + rng.normal(0, 50, n)
    hydro = 3000 + rng.normal(0, 300, n)
    imports = rng.normal(0, 800, n)
    fossil = np.clip(load - solar - wind - nuclear - hydro - imports, 0, None)
    price = 50 + 0.004 * fossil + rng.normal(0, 4, n)
    return pd.DataFrame(
        {
            "zone_code": code,
            "hour_start_utc": hours,
            "delivery_date_local": hours.tz_convert(None).normalize(),
            "delivery_hour_local": hour,
            "utc_offset_minutes": 0,
            "actual_load_mw": load,
            "forecast_error_mw": rng.normal(0, 300, n),
            "wind_mw": wind,
            "solar_mw": solar,
            "nuclear_mw": nuclear,
            "fossil_mw": fossil,
            "hydro_other_mw": hydro,
            "net_import_mw": imports,
            "price_eur_mwh": price,
        }
    )


@pytest.fixture(scope="session")
def make_zone() -> Callable[..., pd.DataFrame]:
    return synthetic_zone
