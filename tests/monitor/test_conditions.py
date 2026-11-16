"""The monitor on synthetic zones whose normal behaviour and anomalies are known."""

import numpy as np
import pandas as pd
import pytest

from power_market_lens.monitor import conditions
from power_market_lens.monitor.conditions import assess, assess_zone, fit, score, trailing_z

NUCLEAR_TRIP = pd.date_range("2025-03-10 08:00", periods=6, freq="h", tz="UTC")
PRICE_SPIKE = pd.date_range("2025-04-15 12:00", periods=4, freq="h", tz="UTC")


def with_anomalies(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    trip = frame["hour_start_utc"].isin(NUCLEAR_TRIP)
    frame.loc[trip, "nuclear_mw"] = 0.0  # a reactor stops; nothing else changes
    spike = frame["hour_start_utc"].isin(PRICE_SPIKE)
    frame.loc[spike, "price_eur_mwh"] = 400.0  # a price its fossil output does not explain
    return frame


@pytest.fixture(scope="module")
def scored(make_zone) -> pd.DataFrame:
    return assess_zone(with_anomalies(make_zone()))


def test_about_one_to_two_percent_of_normal_hours_are_unusual(scored):
    # Two statistics, each at its reference 99th percentile: by construction
    # between 1% (they always agree) and 2% (never), plus quantile interpolation.
    reference = scored[scored["is_reference"]]
    assert 0.01 <= reference["is_unusual"].mean() <= 0.025
    clean = scored[
        ~scored["is_reference"] & ~scored["hour_start_utc"].isin(NUCLEAR_TRIP.union(PRICE_SPIKE))
    ]
    assert clean["is_unusual"].mean() < 0.04


def test_a_reactor_stopping_is_flagged_and_blamed_on_nuclear(scored):
    trip = scored[scored["hour_start_utc"].isin(NUCLEAR_TRIP)]

    assert trip["is_unusual"].all()
    assert (trip["driver_1"] == "nuclear_mw").all()
    assert (trip["driver_1_z"] < -10).all()  # far below its usual level


def test_a_price_its_fuel_does_not_explain_is_blamed_on_the_price(scored):
    spike = scored[scored["hour_start_utc"].isin(PRICE_SPIKE)]

    assert spike["is_unusual"].all()
    assert (spike["driver_1"] == "price_eur_mwh").all()
    assert (spike["driver_1_z"] > 0).all()


def test_the_first_four_weeks_have_no_score(make_zone):
    scored = assess_zone(make_zone())

    assert scored["hour_start_utc"].min() >= pd.Timestamp("2024-01-29", tz="UTC")


def test_a_z_score_uses_only_the_past(make_zone):
    frame = make_zone(end="2024-03-31 23:00")
    features = conditions.present_features(frame)
    # The std floor comes from the reference period, which lies in the past
    # of every hour the monitor reports on; the changed hour is after it.
    reference = (frame["hour_start_utc"] < pd.Timestamp("2024-03-01", tz="UTC")).to_numpy()
    before = trailing_z(frame, features, reference)

    changed = frame.copy()
    changed.loc[changed.index[-1], "actual_load_mw"] *= 10
    after = trailing_z(changed, features, reference)

    pd.testing.assert_frame_equal(before.iloc[:-1], after.iloc[:-1])


def test_solar_at_night_cannot_explode(make_zone):
    # Night-time solar is exactly zero every day: its trailing std is zero,
    # and only the floor keeps the z-scores finite.
    frame = make_zone(end="2024-03-31 23:00")
    frame.loc[frame["delivery_hour_local"] < 6, "solar_mw"] = 0.0
    features = conditions.present_features(frame)

    z = trailing_z(frame, features, np.ones(len(frame), dtype=bool)).dropna()

    assert np.isfinite(z.to_numpy()).all()


def test_contributions_add_up_to_their_statistic(make_zone):
    frame = make_zone(end="2024-06-30 23:00")
    features = conditions.present_features(frame)
    z = trailing_z(frame, features, np.ones(len(frame), dtype=bool)).dropna().to_numpy()
    model = fit(z, features)

    t2, q, t2_contributions, q_contributions = score(model, z)

    np.testing.assert_allclose(t2_contributions.sum(axis=1), t2)
    np.testing.assert_allclose(q_contributions.sum(axis=1), q)
    assert 1 <= model.n_components < len(features)


def test_a_variable_the_zone_does_not_have_is_left_out(make_zone):
    frame = make_zone()
    frame["nuclear_mw"] = np.nan  # Italy

    scored = assess_zone(frame)

    assert not scored.empty
    assert (scored["n_features"] == 8).all()
    assert "nuclear_mw" not in set(scored["driver_1"])


def test_a_zone_without_enough_reference_history_is_not_scored(make_zone):
    late = make_zone()
    late = late[late["hour_start_utc"] >= pd.Timestamp("2024-12-01", tz="UTC")]

    assert assess_zone(late).empty


def test_each_zone_is_scored_against_its_own_history(make_zone):
    both = pd.concat([make_zone("ES", seed=1), make_zone("FR", seed=2)])

    scored = assess(both)

    assert set(scored["zone_code"]) == {"ES", "FR"}
    per_zone = scored.groupby("zone_code")["t2_limit"]
    assert (per_zone.nunique() == 1).all()  # one model per zone
    assert per_zone.first().nunique() == 2
