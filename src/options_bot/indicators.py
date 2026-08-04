"""Small deterministic indicators extracted from the notebook prototype."""

from __future__ import annotations

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
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("OHLC sequences must have equal length")
    if len(closes) <= period:
        return None
    ranges = [
        true_range(float(highs[index]), float(lows[index]), float(closes[index - 1]))
        for index in range(1, len(closes))
    ]
    value = sum(ranges[:period]) / period
    for item in ranges[period:]:
        value = (value * (period - 1) + item) / period
    return value
