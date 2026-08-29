"""Experimental strategies -- alternatives to MomentumStrategy's
trend-following approach, for checking whether a genuinely different signal
shape does better than parameter-tuning the existing one. Each implements
the same duck-typed interface MomentumStrategy does (evaluate(candles) ->
Signal | None, plus minimum_candles/rsi_period/etc.) so they drop into the
existing backtest engines unchanged.

``TrendConfirmedMomentumStrategy`` ("Candidate B") was imported into
connections.py (the live/forward-paper path) on 2026-08-24, wiring the
project's best-proven strategy into live paper trading -- see the
CANDIDATE_B_* constants near the top of connections.py for the exact
parameters and provenance, and research/INDEX.md's "Current best candidate"
section for the validation history. It was still labeled "Open, not
Confirmed" there at the time this was wired in (see that entry for why, and
what would confirm it) -- a deliberate, informed choice, not an oversight.
The other strategies in this file remain backtest-only, used only through
run_upstox_backtest for research.
"""

from __future__ import annotations

from dataclasses import dataclass

from .candles import Candle
from .indicators import atr, bollinger_bands, ema, rsi
from .strategy import Direction, Signal


@dataclass(frozen=True)
class MeanReversionStrategy:
    """Fades price extremes instead of following trend: BULLISH when price
    closes below the lower Bollinger Band while RSI is oversold (expecting
    a bounce back toward the mean), BEARISH on the mirror-image overbought
    case. A structurally different edge shape than every trend-following
    variant (Baseline, RSI/ATR filters, ML) tested elsewhere in this
    project's research."""

    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    atr_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    minimum_candles: int = 30

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        if len(candles) < self.minimum_candles:
            return None
        closes = [item.close for item in candles]
        _, upper, lower = bollinger_bands(closes, self.bb_period, self.bb_std)
        momentum = rsi(closes, self.rsi_period)[-1]
        volatility = atr(
            [item.high for item in candles], [item.low for item in candles], closes, self.atr_period
        )
        if upper[-1] is None or lower[-1] is None or momentum is None or volatility is None or volatility <= 0:
            return None
        close = closes[-1]
        # Strict inequality: with zero recent variance (a flat run), the
        # bands collapse onto the mean and close sits exactly on both --
        # that's "no signal" (nothing to revert from), not a breakout.
        if close < lower[-1] and momentum <= self.rsi_oversold:
            confidence = min(0.95, 0.5 + (self.rsi_oversold - momentum) / 100)
            return Signal(Direction.BULLISH, confidence, volatility, "below lower Bollinger band, RSI oversold")
        if close > upper[-1] and momentum >= self.rsi_overbought:
            confidence = min(0.95, 0.5 + (momentum - self.rsi_overbought) / 100)
            return Signal(Direction.BEARISH, confidence, volatility, "above upper Bollinger band, RSI overbought")
        return None


@dataclass(frozen=True)
class OpeningRangeBreakoutStrategy:
    """Trades breaks of the session's opening range (the high/low of the
    first ``opening_range_bars`` candles each day) instead of an indicator
    crossover -- a session-structure-based timing rule, not a trend or
    mean-reversion one."""

    opening_range_bars: int = 3  # 3 x 5-minute bars = first 15 minutes
    atr_period: int = 14
    # Doesn't use RSI in its own decision -- only present because
    # generate_signals_from_candles records an RSI value on every
    # observation regardless of strategy (for filtering/ML-feature use).
    rsi_period: int = 14
    minimum_candles: int = 20

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        if len(candles) < self.minimum_candles:
            return None
        last = candles[-1]
        today = last.started_at.date()
        todays_candles = [item for item in candles if item.started_at.date() == today]
        if len(todays_candles) <= self.opening_range_bars:
            return None
        opening = todays_candles[: self.opening_range_bars]
        range_high = max(item.high for item in opening)
        range_low = min(item.low for item in opening)
        closes = [item.close for item in candles]
        volatility = atr(
            [item.high for item in candles], [item.low for item in candles], closes, self.atr_period
        )
        if volatility is None or volatility <= 0:
            return None
        close = closes[-1]
        if close > range_high:
            confidence = min(0.95, 0.5 + (close - range_high) / range_high)
            return Signal(Direction.BULLISH, confidence, volatility, "broke above opening range high")
        if close < range_low:
            confidence = min(0.95, 0.5 + (range_low - close) / range_low)
            return Signal(Direction.BEARISH, confidence, volatility, "broke below opening range low")
        return None


@dataclass(frozen=True)
class TrendConfirmedMomentumStrategy:
    """MomentumStrategy's exact rule, plus a slower macro-trend EMA that
    must agree with the direction -- a multi-timeframe-style confirmation
    using differently-scaled EMAs on the same candle series (avoiding the
    need to fetch/align a genuinely separate coarser timeframe)."""

    fast_period: int = 9
    slow_period: int = 21
    macro_period: int = 60
    rsi_period: int = 14
    atr_period: int = 14
    minimum_candles: int = 70

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        if len(candles) < self.minimum_candles:
            return None
        closes = [item.close for item in candles]
        fast = ema(closes, self.fast_period)[-1]
        slow = ema(closes, self.slow_period)[-1]
        macro = ema(closes, self.macro_period)[-1]
        momentum = rsi(closes, self.rsi_period)[-1]
        volatility = atr(
            [item.high for item in candles], [item.low for item in candles], closes, self.atr_period
        )
        return self.signal_from_indicators_with_macro(fast, slow, macro, momentum, volatility, closes[-1])

    def signal_from_indicators_with_macro(
        self,
        fast: float,
        slow: float,
        macro: float,
        momentum: float | None,
        volatility: float | None,
        close: float,
    ) -> Signal | None:
        """The decision rule alone, given already-computed indicator values --
        see MomentumStrategy.signal_from_indicators for why this split
        exists (lets upstox_backtest.generate_signals_from_candles compute
        each indicator series once instead of recomputing per step)."""
        if momentum is None or volatility is None or volatility <= 0:
            return None
        if fast > slow and 52 <= momentum <= 75 and close > macro:
            return Signal(
                Direction.BULLISH, min(0.95, 0.5 + (momentum - 50) / 100), volatility,
                "EMA trend + RSI + macro-trend confirmed bullish",
            )
        if fast < slow and 25 <= momentum <= 48 and close < macro:
            return Signal(
                Direction.BEARISH, min(0.95, 0.5 + (50 - momentum) / 100), volatility,
                "EMA trend + RSI + macro-trend confirmed bearish",
            )
        return None
