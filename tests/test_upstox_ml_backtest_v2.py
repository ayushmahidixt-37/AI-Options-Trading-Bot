from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
from options_bot.upstox_ml_backtest_v2 import run_upstox_ml_backtest_v2

IST = ZoneInfo("Asia/Kolkata")

_ALWAYS_ACCEPT = SignalQualityModel(
    feature_names=(), means=(), stds=(), weights=(), bias=10.0, threshold=0.5, metadata={}
)
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


def test_v2_reports_insufficient_data_on_empty_archive(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    assert run_upstox_ml_backtest_v2(archive, model=_ALWAYS_ACCEPT).status == "INSUFFICIENT DATA"


def test_v2_with_always_accept_matches_the_unfiltered_backtest(tmp_path) -> None:
    """Same scenario as v1's test_always_accept_model_reproduces_the_unfiltered_backtest_exactly."""
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
    v2_result = run_upstox_ml_backtest_v2(archive, model=_ALWAYS_ACCEPT, strategy=strategy, settings=settings, parameters=params)

    assert v2_result.trades == baseline.trades == 1
    assert v2_result.net_pnl == baseline.net_pnl
    assert v2_result.trade_details[0].exit_reason == baseline.trade_details[0].exit_reason
    assert v2_result.trade_details[0].exit_price == baseline.trade_details[0].exit_price


def _seed_three_signal_scenario(tmp_path):
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
    ce_start = start + timedelta(minutes=15)  # 09:30
    ce_candles = [
        UpstoxCandle("NIFTY13AUG2626600CE", ce_start + timedelta(minutes=5 * i), 100 + 5 * i, 102 + 5 * i, 99 + 5 * i, 101 + 5 * i)
        for i in range(5)  # 09:30..09:50
    ]
    archive.save_upstox_candles(
        ce_candles, token="NSE_FO|1|13-08-2026", exchange="NFO", timeframe="FIVE_MINUTE",
        collected_at=ce_start + timedelta(minutes=20),
    )
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")})
    strategy = ScriptedStrategy(
        {
            3: Signal(Direction.BULLISH, 0.6, 10.0, "test"),   # 09:25
            5: Signal(Direction.BEARISH, 0.6, 10.0, "test"),   # 09:35, rejected by the model
            8: Signal(Direction.BULLISH, 0.6, 10.0, "test"),   # 09:50
        }
    )
    params = BacktestParameters(stop_risk_fraction=None)
    return archive, strategy, settings, params, ce_start


def test_v2_preserves_v1s_exit_boundary_sequencing_for_a_precontract_only_model(tmp_path) -> None:
    """The exact scenario that guards v1's core correctness property (see
    upstox_ml_backtest.py's test_ml_filter_runs_before_trade_construction_not_after)
    must produce byte-identical results through v2 with the same precontract-only
    model -- proof that moving contract selection earlier didn't change which
    observations survive or what boundaries they define."""
    archive, strategy, settings, params, ce_start = _seed_three_signal_scenario(tmp_path)

    v1_result = run_upstox_ml_backtest(
        archive, model=_REJECT_BEARISH, strategy=strategy, settings=settings, parameters=params
    )
    v2_result = run_upstox_ml_backtest_v2(
        archive, model=_REJECT_BEARISH, strategy=strategy, settings=settings, parameters=params
    )

    assert v2_result.trades == v1_result.trades == 1
    v1_trade, v2_trade = v1_result.trade_details[0], v2_result.trade_details[0]
    assert v2_trade.exit_reason == v1_trade.exit_reason == "signal-reversal"
    assert v2_trade.exit_at == v1_trade.exit_at == ce_start + timedelta(minutes=20)
    assert v2_trade.exit_price == v1_trade.exit_price
    assert v2_trade.net_pnl == v1_trade.net_pnl


def test_v2_open_interest_feature_actually_drives_the_decision(tmp_path) -> None:
    """A model that only looks at open_interest_normalized must keep a
    signal whose selected contract has high OI and reject one with low OI
    -- proving the postcontract feature actually reaches the ML decision,
    not just that it's computed and ignored."""
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
    # The signal fires at candles[1] (start+5min, window length 2). The OI
    # lookup takes the most recent candle at or before that moment, so the
    # OI-bearing candle must be <= start+5min; a separate later candle
    # (> start+5min) provides the actual entry fill.
    entry_at = start + timedelta(minutes=10)
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG2626600CE", start, 100, 101, 99, 100, open_interest=1_000_000),
            UpstoxCandle("NIFTY13AUG2626600CE", entry_at, 100, 108, 99, 106),
        ],
        token="NSE_FO|1|13-08-2026", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=entry_at,
    )
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")})
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})
    params = BacktestParameters(stop_risk_fraction=None)
    # log1p(1_000_000)/15 ~= 0.921, log1p(1)/15 ~= 0.046 -- bias=-0.5 puts
    # sigmoid(-0.5+0.921)=0.604 (kept) and sigmoid(-0.5+0.046)=0.388
    # (rejected) on opposite sides of the 0.5 threshold.
    oi_gate = SignalQualityModel(
        feature_names=("open_interest_normalized",),
        means=(0.0,), stds=(1.0,), weights=(1.0,), bias=-0.5, threshold=0.5, metadata={},
    )

    kept = run_upstox_ml_backtest_v2(archive, model=oi_gate, strategy=strategy, settings=settings, parameters=params)
    assert kept.trades == 1

    archive_low_oi = MarketArchive(tmp_path / "market_low_oi.sqlite3")
    archive_low_oi.initialize()
    archive_low_oi.save_upstox_candles(
        [UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close) for c in underlying],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=start,
    )
    archive_low_oi.save_instruments(
        [Instrument("NIFTY13AUG2626600CE", "NSE_FO|1|13-08-2026", "NFO", "NIFTY", "CE", 75, date(2026, 8, 13), 100)],
        start,
    )
    archive_low_oi.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG2626600CE", start, 100, 101, 99, 100, open_interest=1),
            UpstoxCandle("NIFTY13AUG2626600CE", entry_at, 100, 108, 99, 106),
        ],
        token="NSE_FO|1|13-08-2026", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=entry_at,
    )
    rejected = run_upstox_ml_backtest_v2(archive_low_oi, model=oi_gate, strategy=strategy, settings=settings, parameters=params)
    assert rejected.trades == 0


def test_v2_handles_a_signal_with_no_available_contract(tmp_path) -> None:
    """A signal whose direction has no matching contract must not crash
    postcontract feature extraction -- open_interest_known must read as
    False rather than raise."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    underlying = _underlying_candles(4, start=start)
    archive.save_upstox_candles(
        [UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close) for c in underlying],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=start,
    )
    # No instruments saved at all -- no contract can ever be found.
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")})
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})
    model = SignalQualityModel(
        feature_names=("open_interest_normalized", "days_to_expiry"),
        means=(0.0, 0.0), stds=(1.0, 1.0), weights=(0.0, 0.0), bias=10.0, threshold=0.5, metadata={},
    )

    result = run_upstox_ml_backtest_v2(archive, model=model, strategy=strategy, settings=settings)
    assert result.status == "INSUFFICIENT DATA"
    assert result.trades == 0
