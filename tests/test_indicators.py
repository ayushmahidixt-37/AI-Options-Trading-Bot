import pytest

from options_bot.indicators import atr, ema, rsi, sma


def test_moving_averages_are_aligned() -> None:
    assert sma([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    assert ema([10, 20, 30], 3) == [10.0, 15.0, 22.5]


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
