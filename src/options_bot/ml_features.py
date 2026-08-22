"""Pure feature extraction for the ML signal-quality filter.

Deliberately independent of ``strategy.py`` and ``upstox_backtest.py`` --
recomputes EMA9/EMA21 from the same candle window ``generate_signals_from_candles``
already used to produce a ``SyntheticObservation``, via the existing pure
``indicators.ema`` function, rather than changing either module's tested
contract. ``rsi``/``atr``/``confidence`` are read straight off the passed-in
observation (no reimplementation, no drift risk between two RSI/ATR paths).

Split into a pre-contract and a post-contract function because open interest
and days-to-expiry are properties of the *selected option contract*, which is
only known after contract selection runs inside the backtest engine -- later
than the point at which entry-filter features are needed. v1 models are
trained on ``FEATURE_NAMES`` (precontract only); ``POSTCONTRACT_FEATURE_NAMES``
exists so a later model can add contract-level features without reworking
this module.

``is_macro_event_window`` (added 2026-08-22) flags a signal observed on, or
the trading day after, a known scheduled RBI/FOMC/Budget date -- see
``market_events.py``. Motivated by a real, honestly-tested finding for the
``TrendConfirmedMomentumStrategy`` candidate: macro-event-window trades
averaged more than double the per-trade P&L of ordinary days (825-trade
sample, BACKTEST_FINDINGS.md's 2026-08-22 "known-event calendar" entry) --
the opposite of the naive "avoid news days" intuition. Whether this feature
also helps *this* ML filter (trained on plain ``MomentumStrategy`` signals,
a different base strategy) is exactly what needs testing, not assumed.
Existing saved models (``research/models/*.json``) are unaffected -- each
carries its own ``feature_names`` tuple, not this module-level constant, so
older models keep scoring with their original 7 features.
"""

from __future__ import annotations

import math
from datetime import date, datetime

from .candles import Candle
from .indicators import ema
from .market_events import is_macro_event_window
from .strategy import MomentumStrategy
from .upstox_backtest import SyntheticObservation

_SESSION_OPEN_MINUTES = 9 * 60 + 15  # 09:15 IST

FEATURE_NAMES: tuple[str, ...] = (
    "rsi",
    "atr_normalized",
    "ema_gap_normalized",
    "confidence",
    "direction_is_bullish",
    "minutes_since_open",
    "day_of_week",
    "is_macro_event_window",
)

POSTCONTRACT_FEATURE_NAMES: tuple[str, ...] = (
    "days_to_expiry",
    "open_interest_known",
    "open_interest_normalized",
)


def _window_for(candles: list[Candle], observed_at: datetime) -> list[Candle]:
    return [candle for candle in candles if candle.started_at <= observed_at]


def extract_features_precontract(
    candles: list[Candle], observation: SyntheticObservation, strategy: MomentumStrategy
) -> dict[str, float]:
    """Technical + time-of-day features available before contract selection."""
    window = _window_for(candles, observation.observed_at)
    if not window:
        raise ValueError("no candles at or before this observation's timestamp")
    closes = [item.close for item in window]
    close = window[-1].close
    ema_fast = ema(closes, strategy.fast_period)[-1]
    ema_slow = ema(closes, strategy.slow_period)[-1]
    rsi_value = observation.rsi if observation.rsi is not None else 50.0
    minutes_since_open = observation.observed_at.hour * 60 + observation.observed_at.minute - _SESSION_OPEN_MINUTES
    return {
        "rsi": rsi_value,
        "atr_normalized": observation.atr / close if close else 0.0,
        "ema_gap_normalized": abs(ema_fast - ema_slow) / close if close else 0.0,
        "confidence": observation.confidence,
        "direction_is_bullish": 1.0 if observation.signal == "BULLISH" else 0.0,
        "minutes_since_open": float(minutes_since_open),
        "day_of_week": float(observation.observed_at.weekday()),
        "is_macro_event_window": 1.0 if is_macro_event_window(observation.observed_at.date()) else 0.0,
    }


def extract_features_postcontract(
    observation: SyntheticObservation, expiry: date, open_interest: float | None
) -> dict[str, float]:
    """Contract-level features, only available after contract selection."""
    known = open_interest is not None
    return {
        "days_to_expiry": float((expiry - observation.observed_at.date()).days),
        "open_interest_known": 1.0 if known else 0.0,
        "open_interest_normalized": math.log1p(open_interest) / 15 if known else 0.0,
    }
