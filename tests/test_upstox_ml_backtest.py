from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from options_bot.backtest import BacktestParameters
from options_bot.candles import Candle
from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive
from options_bot.ml_model import SignalQualityModel
from options_bot.strategy import Direction, Signal
from options_bot.upstox_backtest import run_upstox_backtest
from options_bot.upstox_data import UpstoxCandle
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY
from options_bot.upstox_ml_backtest import run_upstox_ml_backtest

IST = ZoneInfo("Asia/Kolkata")

_ALWAYS_ACCEPT = SignalQualityModel(
    feature_names=(), means=(), stds=(), weights=(), bias=10.0, threshold=0.5, metadata={}
)
# Rejects any signal whose `direction_is_bullish` feature is 0.0 (BEARISH),
# accepts anything with direction_is_bullish == 1.0 (BULLISH).
_REJECT_BEARISH = SignalQualityModel(
    feature_names=("direction_is_bullish",),
    means=(0.0,), stds=(1.0,), weights=(20.0,), bias=-10.0, threshold=0.5, metadata={},
)


def _underlying_candles(count: int, start: datetime) -> list[Candle]:
    return [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(count)
    ]


class ScriptedStrategy:
    minimum_candles = 2
    rsi_period = 14
    fast_period = 9
    slow_period = 21

    def __init__(self, signals: dict[int, Signal]) -> None:
        self._signals = signals

    def evaluate(self, history: list[Candle]) -> Signal | None:
        return self._signals.get(len(history))


def test_run_upstox_ml_backtest_reports_insufficient_data_on_empty_archive(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()

    assert run_upstox_ml_backtest(archive, model=_ALWAYS_ACCEPT).status == "INSUFFICIENT DATA"


def test_run_upstox_ml_backtest_rejects_a_model_requesting_postcontract_features(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    model = SignalQualityModel(
        feature_names=("days_to_expiry",), means=(0.0,), stds=(1.0,), weights=(1.0,), bias=0.0,
        threshold=0.5, metadata={},
    )

    with pytest.raises(ValueError):
        run_upstox_ml_backtest(archive, model=model)


def test_always_accept_model_reproduces_the_unfiltered_backtest_exactly(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    underlying = _underlying_candles(4, start=start)
    archive.save_upstox_candles(
        [UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close) for c in underlying],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=start,
    )
    archive.save_instruments(
        [Instrument("NIFTY13AUG2626600CE", "NSE_FO|1|13-08-2026", "NFO", "NIFTY", "CE", 75, date(2026, 8, 13), 100)],
        start,
    )
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=10), 100, 108, 99, 106),
            UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=15), 106, 109, 104, 105),
        ],
        token="NSE_FO|1|13-08-2026", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=start + timedelta(minutes=15),
    )
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")})
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})
    params = BacktestParameters(stop_risk_fraction=None)

    baseline = run_upstox_backtest(archive, strategy=strategy, settings=settings, parameters=params)
    ml_result = run_upstox_ml_backtest(archive, model=_ALWAYS_ACCEPT, strategy=strategy, settings=settings, parameters=params)

    assert ml_result.trades == baseline.trades == 1
    assert ml_result.net_pnl == baseline.net_pnl
    assert ml_result.trade_details[0].exit_reason == baseline.trade_details[0].exit_reason
    assert ml_result.trade_details[0].exit_price == baseline.trade_details[0].exit_price


def test_ml_filter_runs_before_trade_construction_not_after(tmp_path) -> None:
    """Regression test for the correctness trap this module's docstring warns
    about: a rejected signal sitting between two accepted ones must not act
    as a premature exit trigger for the trade before it.

    Three alternating signals (BULLISH at 09:25, BEARISH at 09:35, BULLISH at
    09:50) are generated; the ML model rejects the middle BEARISH one. The
    first (BULLISH) trade's exit-by-signal-reversal boundary must extend all
    the way to the *surviving* next observation at 09:50 (exit price 121,
    from that time's close), not stop early at the *rejected* observation's
    time of 09:35 (which would give exit price 106 -- a naive post-hoc filter
    over an unfiltered run would produce exactly that wrong number).
    """
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    underlying = _underlying_candles(12, start=start)
    archive.save_upstox_candles(
        [UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close) for c in underlying],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=start,
    )
    archive.save_instruments(
        [Instrument("NIFTY13AUG2626600CE", "NSE_FO|1|13-08-2026", "NFO", "NIFTY", "CE", 75, date(2026, 8, 13), 100)],
        start,
    )
    # CE candles every 5 minutes from 09:30 to 09:50 -- entry fills at 09:30's
    # open (100); if the exit boundary is correctly 09:50 the exit close is
    # 121, if it wrongly stops at 09:35 (the rejected observation's time) the
    # exit close is 106.
    ce_start = start + timedelta(minutes=15)  # 09:30
    ce_candles = [
        UpstoxCandle("NIFTY13AUG2626600CE", ce_start + timedelta(minutes=5 * i), 100 + 5 * i, 102 + 5 * i, 99 + 5 * i, 101 + 5 * i)
        for i in range(5)  # 09:30, 09:35, 09:40, 09:45, 09:50
    ]
    archive.save_upstox_candles(
        ce_candles, token="NSE_FO|1|13-08-2026", exchange="NFO", timeframe="FIVE_MINUTE",
        collected_at=ce_start + timedelta(minutes=20),
    )
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")})
    strategy = ScriptedStrategy(
        {
            3: Signal(Direction.BULLISH, 0.6, 10.0, "test"),   # candles[2] -> 09:25
            5: Signal(Direction.BEARISH, 0.6, 10.0, "test"),   # candles[4] -> 09:35 (rejected by the model)
            8: Signal(Direction.BULLISH, 0.6, 10.0, "test"),   # candles[7] -> 09:50
        }
    )
    params = BacktestParameters(stop_risk_fraction=None)

    result = run_upstox_ml_backtest(
        archive, model=_REJECT_BEARISH, strategy=strategy, settings=settings, parameters=params
    )

    assert result.trades == 1  # the BEARISH signal produced no trade; the third BULLISH had no CE data after it
    trade = result.trade_details[0]
    assert trade.exit_reason == "signal-reversal"
    assert trade.exit_at == ce_start + timedelta(minutes=20)  # 09:50, the surviving observation's time
    slippage = settings.paper_slippage_bps / 10_000
    assert trade.exit_price == round(121 * (1 - slippage), 2)
