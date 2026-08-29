from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.candles import Candle
from options_bot.ml_features import (
    FEATURE_NAMES,
    POSTCONTRACT_FEATURE_NAMES,
    extract_features_postcontract,
    extract_features_precontract,
)
from options_bot.strategy import MomentumStrategy
from options_bot.upstox_backtest import SyntheticObservation

IST = ZoneInfo("Asia/Kolkata")


def _candles(count: int, start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    return [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(count)
    ]


def test_extract_features_precontract_returns_all_declared_feature_names() -> None:
    candles = _candles(60)
    observation = SyntheticObservation(
        observed_at=candles[-1].started_at, spot=101.0, signal="BULLISH", rsi=60.0, atr=5.0, confidence=0.6
    )
    features = extract_features_precontract(candles, observation, MomentumStrategy())

    assert set(features) == set(FEATURE_NAMES)
    assert features["direction_is_bullish"] == 1.0
    assert features["rsi"] == 60.0
    assert features["confidence"] == 0.6


def test_extract_features_precontract_only_uses_candles_at_or_before_observation() -> None:
    candles = _candles(60)
    cutoff = candles[30].started_at
    observation = SyntheticObservation(
        observed_at=cutoff, spot=candles[30].close, signal="BEARISH", rsi=40.0, atr=3.0, confidence=0.55
    )
    features_full = extract_features_precontract(candles, observation, MomentumStrategy())
    features_truncated = extract_features_precontract(candles[:31], observation, MomentumStrategy())

    assert features_full == features_truncated


def test_minutes_since_open_and_day_of_week_are_computed_from_the_observation_timestamp() -> None:
    candles = _candles(60)
    monday_930 = datetime(2026, 8, 3, 9, 30, tzinfo=IST)  # 2026-08-03 is a Monday
    observation = SyntheticObservation(
        observed_at=monday_930, spot=101.0, signal="BULLISH", rsi=60.0, atr=5.0, confidence=0.6
    )
    features = extract_features_precontract(candles, observation, MomentumStrategy())

    assert features["minutes_since_open"] == 15.0  # 09:30 - 09:15
    assert features["day_of_week"] == 0.0  # Monday


def test_direction_is_bullish_flag_matches_signal_direction() -> None:
    candles = _candles(60)
    observation = SyntheticObservation(
        observed_at=candles[-1].started_at, spot=101.0, signal="BEARISH", rsi=30.0, atr=5.0, confidence=0.6
    )
    features = extract_features_precontract(candles, observation, MomentumStrategy())

    assert features["direction_is_bullish"] == 0.0


def test_missing_rsi_falls_back_to_neutral_fifty_instead_of_crashing() -> None:
    candles = _candles(60)
    observation = SyntheticObservation(
        observed_at=candles[-1].started_at, spot=101.0, signal="BULLISH", rsi=None, atr=5.0, confidence=0.6
    )
    features = extract_features_precontract(candles, observation, MomentumStrategy())

    assert features["rsi"] == 50.0


def test_extract_features_postcontract_flags_missing_open_interest_explicitly() -> None:
    observation = SyntheticObservation(
        observed_at=datetime(2026, 8, 3, 9, 30, tzinfo=IST),
        spot=101.0, signal="BULLISH", rsi=60.0, atr=5.0, confidence=0.6,
    )
    from datetime import date

    known = extract_features_postcontract(observation, expiry=date(2026, 8, 13), open_interest=1000.0)
    unknown = extract_features_postcontract(observation, expiry=date(2026, 8, 13), open_interest=None)

    assert set(known) == set(POSTCONTRACT_FEATURE_NAMES)
    assert known["open_interest_known"] == 1.0
    assert known["open_interest_normalized"] > 0.0
    assert unknown["open_interest_known"] == 0.0
    assert unknown["open_interest_normalized"] == 0.0
    assert known["days_to_expiry"] == 10.0
