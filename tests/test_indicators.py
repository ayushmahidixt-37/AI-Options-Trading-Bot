import math

import pytest

from options_bot.indicators import atr, bollinger_bands, ema, rsi, sma


def test_moving_averages_are_aligned() -> None:
    assert sma([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    assert ema([10, 20, 30], 3) == [10.0, 15.0, 22.5]


def test_bollinger_bands_warmup_and_values() -> None:
    middle, upper, lower = bollinger_bands([1, 2, 3, 4, 5], period=3, num_std=2.0)
    assert middle[:2] == [None, None]
    assert upper[:2] == [None, None]
    assert lower[:2] == [None, None]
    assert middle[2] == pytest.approx(2.0)
    deviation = math.sqrt(((1 - 2) ** 2 + (2 - 2) ** 2 + (3 - 2) ** 2) / 3)
    assert upper[2] == pytest.approx(2.0 + 2 * deviation)
    assert lower[2] == pytest.approx(2.0 - 2 * deviation)
    # A flat series has zero variance -- bands must collapse onto the mean.
    flat_middle, flat_upper, flat_lower = bollinger_bands([5, 5, 5, 5], period=3, num_std=2.0)
    assert flat_upper[2] == flat_lower[2] == flat_middle[2] == pytest.approx(5.0)


def test_rsi_and_atr_warmup() -> None:
    values = list(range(1, 20))
    result = rsi(values, 14)
    assert result[13] is None
    assert result[14] == 100.0
    assert atr(values, values, values, 14) == pytest.approx(1.0)


def test_indicator_validation() -> None:
    with pytest.raises(ValueError):
        sma([1], 0)
    with pytest.raises(ValueError):
        atr([1], [1, 2], [1])
