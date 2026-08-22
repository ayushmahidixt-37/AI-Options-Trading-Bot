from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.candles import Candle
from options_bot.strategy import Direction
from options_bot.strategy_experimental import (
    MeanReversionStrategy,
    OpeningRangeBreakoutStrategy,
    TrendConfirmedMomentumStrategy,
)
from options_bot.upstox_backtest import generate_signals_from_candles

IST = ZoneInfo("Asia/Kolkata")


def test_mean_reversion_returns_none_before_minimum_candles() -> None:
    strategy = MeanReversionStrategy()
    candles = [Candle("NIFTY", datetime(2026, 8, 3, 9, 15, tzinfo=IST), 100, 101, 99, 100)]
    assert strategy.evaluate(candles) is None


def test_mean_reversion_signals_bullish_on_a_sharp_drop_below_the_lower_band() -> None:
    strategy = MeanReversionStrategy(bb_period=20, minimum_candles=25)
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    flat = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100, 101, 99, 100)
        for i in range(24)
    ]
    drop = Candle("NIFTY", start + timedelta(minutes=5 * 24), 100, 100, 68, 70)
    signal = strategy.evaluate(flat + [drop])
    assert signal is not None
    assert signal.direction is Direction.BULLISH


def test_mean_reversion_signals_bearish_on_a_sharp_spike_above_the_upper_band() -> None:
    strategy = MeanReversionStrategy(bb_period=20, minimum_candles=25)
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    flat = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100, 101, 99, 100)
        for i in range(24)
    ]
    spike = Candle("NIFTY", start + timedelta(minutes=5 * 24), 100, 132, 100, 130)
    signal = strategy.evaluate(flat + [spike])
    assert signal is not None
    assert signal.direction is Direction.BEARISH


def test_mean_reversion_returns_none_within_the_bands() -> None:
    strategy = MeanReversionStrategy(bb_period=20, minimum_candles=25)
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    flat = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100, 101, 99, 100)
        for i in range(25)
    ]
    assert strategy.evaluate(flat) is None


def test_opening_range_breakout_returns_none_before_minimum_candles() -> None:
    strategy = OpeningRangeBreakoutStrategy()
    candles = [Candle("NIFTY", datetime(2026, 8, 3, 9, 15, tzinfo=IST), 100, 101, 99, 100)]
    assert strategy.evaluate(candles) is None


def test_opening_range_breakout_signals_bullish_above_the_opening_high() -> None:
    strategy = OpeningRangeBreakoutStrategy(opening_range_bars=3, minimum_candles=10, atr_period=5)
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    opening = [
        Candle("NIFTY", start, 100, 102, 99, 101),
        Candle("NIFTY", start + timedelta(minutes=5), 101, 101, 99, 100),
        Candle("NIFTY", start + timedelta(minutes=10), 100, 101, 98, 99),
    ]
    filler = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100, 101, 99, 100)
        for i in range(3, 10)
    ]
    breakout = Candle("NIFTY", start + timedelta(minutes=5 * 10), 100, 108, 100, 107)
    signal = strategy.evaluate(opening + filler + [breakout])
    assert signal is not None
    assert signal.direction is Direction.BULLISH


def test_opening_range_breakout_signals_bearish_below_the_opening_low() -> None:
    strategy = OpeningRangeBreakoutStrategy(opening_range_bars=3, minimum_candles=10, atr_period=5)
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    opening = [
        Candle("NIFTY", start, 100, 102, 99, 101),
        Candle("NIFTY", start + timedelta(minutes=5), 101, 101, 99, 100),
        Candle("NIFTY", start + timedelta(minutes=10), 100, 101, 98, 99),
    ]
    filler = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100, 101, 99, 100)
        for i in range(3, 10)
    ]
    breakdown = Candle("NIFTY", start + timedelta(minutes=5 * 10), 100, 100, 92, 93)
    signal = strategy.evaluate(opening + filler + [breakdown])
    assert signal is not None
    assert signal.direction is Direction.BEARISH


def test_opening_range_breakout_ignores_a_second_trading_day_correctly() -> None:
    """The opening range must be computed per calendar day, not carried
    over -- a close matching day 1's range shouldn't trigger on day 2."""
    strategy = OpeningRangeBreakoutStrategy(opening_range_bars=3, minimum_candles=10, atr_period=5)
    day1 = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    day1_candles = [
        Candle("NIFTY", day1 + timedelta(minutes=5 * i), 100, 102, 98, 100)
        for i in range(8)
    ]
    day2 = datetime(2026, 8, 4, 9, 15, tzinfo=IST)
    day2_opening = [
        Candle("NIFTY", day2, 100, 101, 99, 100),
        Candle("NIFTY", day2 + timedelta(minutes=5), 100, 101, 99, 100),
        Candle("NIFTY", day2 + timedelta(minutes=10), 100, 101, 99, 100),
    ]
    # 101, within day 2's own opening range -- must not trigger.
    within_range = Candle("NIFTY", day2 + timedelta(minutes=15), 100, 101, 99, 100.5)
    signal = strategy.evaluate(day1_candles + day2_opening + [within_range])
    assert signal is None


def test_trend_confirmed_momentum_returns_none_before_minimum_candles() -> None:
    strategy = TrendConfirmedMomentumStrategy()
    candles = [Candle("NIFTY", datetime(2026, 8, 3, 9, 15, tzinfo=IST), 100, 101, 99, 100)]
    assert strategy.evaluate(candles) is None


def test_trend_confirmed_momentum_returns_none_on_a_flat_series() -> None:
    strategy = TrendConfirmedMomentumStrategy(minimum_candles=70)
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    flat = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100, 100.5, 99.5, 100)
        for i in range(75)
    ]
    assert strategy.evaluate(flat) is None


def test_all_three_strategies_work_through_generate_signals_from_candles() -> None:
    """Exercises the full walk-forward path each strategy actually runs
    through in a real backtest (not just evaluate() in isolation) -- this
    caught a real bug where OpeningRangeBreakoutStrategy lacked the
    rsi_period attribute every strategy needs for observation recording,
    regardless of whether the strategy's own signal logic uses RSI."""
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(80)
    ]
    for strategy in (
        MeanReversionStrategy(minimum_candles=30),
        OpeningRangeBreakoutStrategy(minimum_candles=20, atr_period=5),
        TrendConfirmedMomentumStrategy(minimum_candles=70),
    ):
        generate_signals_from_candles(candles, strategy)  # must not raise


def test_trend_confirmed_momentum_fast_path_matches_naive_full_recompute() -> None:
    """Same proof-of-equivalence discipline as MomentumStrategy's own
    fast-path test: generate_signals_from_candles's precomputed-series path
    (via signal_from_indicators_with_macro) must produce byte-identical
    output to calling evaluate() naively over an ever-growing window --
    this strategy's dev backtest took 76 minutes on the naive path alone
    (BACKTEST_FINDINGS.md, 2026-08-22), so trusting the fast path here
    matters just as much as it did for MomentumStrategy."""
    strategy = TrendConfirmedMomentumStrategy(minimum_candles=70)
    start = datetime(2026, 1, 5, 9, 15, tzinfo=IST)
    candles: list[Candle] = []
    price = 100.0
    for i in range(400):
        price += 3 * math.sin(i / 7) + 0.4 * math.sin(i / 37)
        candles.append(
            Candle("NIFTY", start + timedelta(minutes=5 * i), price, price + 1.5, price - 1.5, price)
        )

    fast_path = generate_signals_from_candles(candles, strategy)

    naive_observations = []
    last_signal: str | None = None
    for index in range(strategy.minimum_candles, len(candles)):
        window = candles[:index]
        signal = strategy.evaluate(window)
        if signal is None:
            continue
        label = signal.direction.value.upper()
        if label == last_signal:
            continue
        last_signal = label
        decision_candle = window[-1]
        naive_observations.append((decision_candle.started_at, decision_candle.close, label, signal.confidence))

    fast_tuples = [(o.observed_at, o.spot, o.signal, o.confidence) for o in fast_path]
    assert fast_tuples == naive_observations
    assert len(fast_tuples) >= 3
    assert {o.signal for o in fast_path} == {"BULLISH", "BEARISH"}
