from __future__ import annotations

from datetime import date

from options_bot.short_strangle_ml_features import FEATURE_NAMES, extract_features, realized_volatility


def test_realized_volatility_needs_at_least_two_points() -> None:
    assert realized_volatility([]) == 0.0
    assert realized_volatility([0.01]) == 0.0
    assert realized_volatility([0.01, -0.01]) > 0.0


def test_extract_features_returns_every_declared_feature_name() -> None:
    features = extract_features(
        entry_day=date(2026, 8, 6),  # a Thursday
        range_high=25050.0,
        range_low=24950.0,
        days_to_expiry=3,
        prior_close=25000.0,
        entry_spot=25010.0,
        trailing_daily_returns=[0.001, -0.002, 0.0015, 0.0005, -0.001, 0.002],
    )
    assert set(features) == set(FEATURE_NAMES)
    assert features["opening_range_pct"] == (25050.0 - 24950.0) / 24950.0
    assert features["day_of_week"] == 3.0  # Thursday
    assert features["days_to_expiry"] == 3.0
    assert features["gap_from_prev_close_pct"] == (25010.0 - 25000.0) / 25000.0
    assert features["realized_vol_5d"] > 0.0
    assert features["realized_vol_20d"] > 0.0


def test_extract_features_handles_no_prior_close_without_lookahead() -> None:
    features = extract_features(
        entry_day=date(2026, 8, 6),
        range_high=25050.0,
        range_low=24950.0,
        days_to_expiry=3,
        prior_close=None,
        entry_spot=25010.0,
        trailing_daily_returns=[],
    )
    assert features["gap_from_prev_close_pct"] == 0.0
    assert features["realized_vol_5d"] == 0.0
    assert features["realized_vol_20d"] == 0.0
