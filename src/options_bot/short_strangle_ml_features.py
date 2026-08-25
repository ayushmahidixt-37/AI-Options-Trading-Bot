"""Pure feature extraction for the short strangle's daily ML entry filter.

Mirrors ``ml_features.py``'s split (pure computation, no DB access) but for
a fundamentally different decision shape: the strangle makes *one* entry
decision per trading day (not one per intrabar signal), so its features are
day-level -- how wide the opening range was, what regime the underlying is
in, and calendar context -- rather than per-candle technicals like RSI/EMA
gap. ``run_short_strangle_backtest``'s existing ``maximum_opening_range_pct``
filter is a single hard cutoff on one of these features (``opening_range_pct``);
giving the model that same number as a feature, alongside volatility regime
and calendar context, lets it learn a richer version of the same idea
instead of replacing it with something unrelated.

All inputs must be computable from data at or before the entry decision --
no lookahead. ``run_short_strangle_backtest`` is the caller responsible for
that guarantee (trailing realized volatility and the previous day's close
are accumulated day-by-day as it walks forward, never queried ahead).
"""

from __future__ import annotations

import math
from datetime import date

from .market_events import is_macro_event_window

FEATURE_NAMES: tuple[str, ...] = (
    "opening_range_pct",
    "day_of_week",
    "days_to_expiry",
    "is_macro_event_window",
    "gap_from_prev_close_pct",
    "realized_vol_5d",
    "realized_vol_20d",
)


def realized_volatility(daily_returns: list[float]) -> float:
    """Population stdev of a trailing window of daily % returns (0.0 if too short to be meaningful)."""
    if len(daily_returns) < 2:
        return 0.0
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((value - mean) ** 2 for value in daily_returns) / len(daily_returns)
    return math.sqrt(variance)


def extract_features(
    *,
    entry_day: date,
    range_high: float,
    range_low: float,
    days_to_expiry: int,
    prior_close: float | None,
    entry_spot: float,
    trailing_daily_returns: list[float],
) -> dict[str, float]:
    """Day-level features available at the strangle's entry-time decision.

    ``trailing_daily_returns`` is the caller's rolling window of daily %
    returns for sessions strictly before ``entry_day`` (most-recent last);
    the 5d/20d windows are simply its last 5/20 entries.
    """
    if range_low <= 0:
        raise ValueError("range_low must be positive")
    opening_range_pct = (range_high - range_low) / range_low
    gap_pct = (entry_spot - prior_close) / prior_close if prior_close else 0.0
    return {
        "opening_range_pct": opening_range_pct,
        "day_of_week": float(entry_day.weekday()),
        "days_to_expiry": float(days_to_expiry),
        "is_macro_event_window": 1.0 if is_macro_event_window(entry_day) else 0.0,
        "gap_from_prev_close_pct": gap_pct,
        "realized_vol_5d": realized_volatility(trailing_daily_returns[-5:]),
        "realized_vol_20d": realized_volatility(trailing_daily_returns[-20:]),
    }
