"""Small deterministic indicators extracted from the notebook prototype."""

from __future__ import annotations

import math
from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Return a simple moving average aligned to the input sequence."""
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(values)
    window_sum = 0.0
    for index, value in enumerate(values):
        window_sum += float(value)
        if index >= period:
            window_sum -= float(values[index - period])
        if index >= period - 1:
            result[index] = window_sum / period
    return result


def bollinger_bands(
    values: Sequence[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (middle, upper, lower) bands aligned to the input sequence.

    Middle is the simple moving average; upper/lower are offset by
    ``num_std`` population standard deviations of the same trailing window.
    ``None`` until warmed up, matching this module's existing fail-closed
    convention (RSI/ATR).
    """
    if period <= 0:
        raise ValueError("period must be positive")
    middle = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = [float(v) for v in values[index - period + 1 : index + 1]]
        mean = middle[index]
        variance = sum((v - mean) ** 2 for v in window) / period
        deviation = math.sqrt(variance)
        upper[index] = mean + num_std * deviation
        lower[index] = mean - num_std * deviation
    return middle, upper, lower


def ema(values: Sequence[float], period: int) -> list[float]:
    """Return an exponentially weighted moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1 - alpha) * result[-1])
    return result


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Return Wilder RSI values aligned to the input sequence."""
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values, values[1:]):
        change = float(current) - float(previous)
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    result[period] = _rsi_value(average_gain, average_loss)
    for index in range(period, len(gains)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index + 1] = _rsi_value(average_gain, average_loss)
    return result


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)


def true_range(high: float, low: float, previous_close: float) -> float:
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float | None:
    """Return the most recent Wilder ATR, or ``None`` until warmed up."""
    series = atr_series(highs, lows, closes, period)
    return series[-1] if series else None


def atr_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> list[float | None]:
    """Return Wilder ATR aligned to the input sequence -- one value per candle.

    ``atr_series(highs, lows, closes, period)[k]`` is exactly
    ``atr(highs[:k+1], lows[:k+1], closes[:k+1], period)`` -- the same
    left-to-right-only recursion, computed once instead of recomputed from
    scratch for every growing prefix. A walk-forward loop that used to call
    the scalar ``atr()`` once per step (quadratic in the number of candles)
    can call this once up front and index into the result instead.
    """
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("OHLC sequences must have equal length")
    n = len(closes)
    result: list[float | None] = [None] * n
    if n <= period:
        return result
    ranges = [
        true_range(float(highs[index]), float(lows[index]), float(closes[index - 1]))
        for index in range(1, n)
    ]
    value = sum(ranges[:period]) / period
    result[period] = value
    for index in range(period, len(ranges)):
        value = (value * (period - 1) + ranges[index]) / period
        result[index + 1] = value
    return result
