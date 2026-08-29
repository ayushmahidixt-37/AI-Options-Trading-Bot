"""Deterministic underlying-direction strategy and option selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .candles import Candle
from .domain import Instrument
from .indicators import atr, ema, rsi


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass(frozen=True)
class Signal:
    direction: Direction
    confidence: float
    stop_distance: float
    reason: str


@dataclass(frozen=True)
class MomentumStrategy:
    fast_period: int = 9
    slow_period: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    minimum_candles: int = 50

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        """Generate direction from closed underlying candles only."""
        if len(candles) < self.minimum_candles:
            return None
        closes = [item.close for item in candles]
        fast = ema(closes, self.fast_period)[-1]
        slow = ema(closes, self.slow_period)[-1]
        momentum = rsi(closes, self.rsi_period)[-1]
        volatility = atr(
            [item.high for item in candles],
            [item.low for item in candles],
            closes,
            self.atr_period,
        )
        return self.signal_from_indicators(fast, slow, momentum, volatility)

    def signal_from_indicators(
        self, fast: float, slow: float, momentum: float | None, volatility: float | None
    ) -> Signal | None:
        """The decision rule alone, given already-computed indicator values.

        Split out from ``evaluate`` so a caller walking many candles forward
        (``upstox_backtest.generate_signals_from_candles``) can compute each
        indicator series once, in one linear pass, and look up a value per
        step here -- instead of ``evaluate`` recomputing EMA/RSI/ATR from
        scratch over an ever-growing window on every single step, which is
        quadratic in the number of candles and became a 12+-hour bottleneck
        once the archive grew large (2026-08-22). Must stay in exact sync
        with ``evaluate``'s own rule -- this *is* that rule, just callable
        with precomputed inputs.
        """
        if momentum is None or volatility is None or volatility <= 0:
            return None
        if fast > slow and 52 <= momentum <= 75:
            return Signal(Direction.BULLISH, min(0.95, 0.5 + (momentum - 50) / 100), volatility, "EMA trend and RSI bullish")
        if fast < slow and 25 <= momentum <= 48:
            return Signal(Direction.BEARISH, min(0.95, 0.5 + (50 - momentum) / 100), volatility, "EMA trend and RSI bearish")
        return None

    def choose_contract(self, signal: Signal, options: list[Instrument], spot: float) -> Instrument | None:
        required_type = "CE" if signal.direction is Direction.BULLISH else "PE"
        matches = [item for item in options if item.option_type == required_type and item.strike]
        return min(matches, key=lambda item: abs(float(item.strike or 0) - spot), default=None)
