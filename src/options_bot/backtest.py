"""Chronological replay helpers that avoid same-bar look-ahead."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .candles import Candle
from .strategy import MomentumStrategy


@dataclass(frozen=True)
class BacktestTrade:
    entered_at: datetime
    exited_at: datetime
    direction: str
    entry: float
    exit: float
    pnl_points: float


def replay_underlying(
    candles: list[Candle], strategy: MomentumStrategy, *, hold_bars: int = 3
) -> list[BacktestTrade]:
    """Replay signals using only candles closed before each entry.

    This validates strategy chronology on underlying candles. It is not an
    options-profitability claim; contract pricing, lots, fees, and paper-broker
    fills are handled by later integration backtests.
    """
    if hold_bars <= 0:
        raise ValueError("hold_bars must be positive")
    trades: list[BacktestTrade] = []
    index = strategy.minimum_candles
    while index + hold_bars < len(candles):
        history = candles[:index]
        signal = strategy.evaluate(history)
        if signal is None:
            index += 1
            continue
        entry_candle = candles[index]
        exit_candle = candles[index + hold_bars]
        multiplier = 1 if signal.direction.value == "bullish" else -1
        points = (exit_candle.close - entry_candle.open) * multiplier
        trades.append(
            BacktestTrade(
                entered_at=entry_candle.started_at,
                exited_at=exit_candle.started_at,
                direction=signal.direction.value,
                entry=entry_candle.open,
                exit=exit_candle.close,
                pnl_points=points,
            )
        )
        index += hold_bars + 1
    return trades
