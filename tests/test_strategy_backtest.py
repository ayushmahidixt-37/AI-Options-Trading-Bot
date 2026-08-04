from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.backtest import replay_underlying
from options_bot.candles import Candle
from options_bot.domain import Instrument
from options_bot.strategy import Direction, MomentumStrategy, Signal


def candles(count: int):
    start = datetime(2026, 8, 3, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    return [
        Candle("NIFTY", start + timedelta(minutes=i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(count)
    ]


def test_strategy_waits_for_warmup_and_selects_correct_option_type() -> None:
    strategy = MomentumStrategy(minimum_candles=50)
    assert strategy.evaluate(candles(49)) is None
    options = [
        Instrument("CE1", "1", "NFO", "NIFTY", "CE", 50, strike=24500),
        Instrument("PE1", "2", "NFO", "NIFTY", "PE", 50, strike=24500),
    ]
    bullish = Signal(Direction.BULLISH, 0.7, 10, "test")
    bearish = Signal(Direction.BEARISH, 0.7, 10, "test")
    assert strategy.choose_contract(bullish, options, 24510).option_type == "CE"
    assert strategy.choose_contract(bearish, options, 24510).option_type == "PE"


def test_backtest_enters_on_next_bar_not_signal_bar() -> None:
    class AlwaysBullish:
        minimum_candles = 2

        def evaluate(self, history):
            return Signal(Direction.BULLISH, 0.7, 1, "test")

    series = candles(8)
    trades = replay_underlying(series, AlwaysBullish(), hold_bars=2)
    assert trades[0].entered_at == series[2].started_at
    assert trades[0].exited_at == series[4].started_at
