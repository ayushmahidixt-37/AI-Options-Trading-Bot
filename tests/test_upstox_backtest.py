from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.candles import Candle
from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive
from options_bot.strategy import Direction, Signal
from options_bot.upstox_backtest import generate_signals_from_candles, run_upstox_backtest
from options_bot.upstox_data import UpstoxCandle
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

IST = ZoneInfo("Asia/Kolkata")


def _underlying_candles(count: int, start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    return [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(count)
    ]


class ScriptedStrategy:
    """A fake strategy that returns a fixed signal at each candle-window length."""

    minimum_candles = 2
    rsi_period = 14

    def __init__(self, signals: dict[int, Signal]) -> None:
        self._signals = signals

    def evaluate(self, history: list[Candle]) -> Signal | None:
        return self._signals.get(len(history))


def test_generate_signals_dedups_and_uses_last_closed_candle() -> None:
    series = _underlying_candles(10)
    signals = {
        3: Signal(Direction.BULLISH, 0.6, 12.0, "test"),
        4: Signal(Direction.BULLISH, 0.6, 12.0, "test"),  # duplicate direction, skipped
        5: Signal(Direction.BEARISH, 0.6, 8.0, "test"),
        7: Signal(Direction.BEARISH, 0.6, 8.0, "test"),  # duplicate direction, skipped
        8: Signal(Direction.BULLISH, 0.6, 12.0, "test"),
    }

    observations = generate_signals_from_candles(series, ScriptedStrategy(signals))

    assert [item.signal for item in observations] == ["BULLISH", "BEARISH", "BULLISH"]
    assert observations[0].observed_at == series[2].started_at
    assert observations[1].observed_at == series[4].started_at
    assert observations[2].observed_at == series[7].started_at
    assert observations[0].atr == 12.0
    assert observations[0].confidence == 0.6


def test_generate_signals_never_evaluates_before_minimum_candles() -> None:
    series = _underlying_candles(5)
    strategy = ScriptedStrategy({1: Signal(Direction.BULLISH, 0.6, 1.0, "test")})
    # minimum_candles=2, so index 1 (a window of length 1) must never be reached
    assert generate_signals_from_candles(series, strategy) == []


def test_run_upstox_backtest_reports_insufficient_data_on_empty_archive(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()

    assert run_upstox_backtest(archive).status == "INSUFFICIENT DATA"


def _seed_upstox_backtest_archive(archive: MarketArchive, start: datetime) -> None:
    underlying = _underlying_candles(4, start=start)
    archive.save_upstox_candles(
        [
            UpstoxCandle(candle.symbol, candle.started_at, candle.open, candle.high, candle.low, candle.close)
            for candle in underlying
        ],
        token=NIFTY_UNDERLYING_KEY,
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=start,
    )
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2626600CE",
                "NSE_FO|1|13-08-2026",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                100,
            )
        ],
        start,
    )
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=10), 100, 108, 99, 106),
            UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=15), 106, 109, 104, 105),
        ],
        token="NSE_FO|1|13-08-2026",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=15),
    )


def test_run_upstox_backtest_replays_a_signal_without_strategy_observations(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    result = run_upstox_backtest(archive, strategy=strategy, settings=settings)

    assert result.trades == 1
    trade = result.trade_details[0]
    assert trade.direction == "BULLISH"
    assert trade.token == "NSE_FO|1|13-08-2026"
    assert trade.exit_reason == "force-exit"
    assert trade.entry_price < trade.exit_price  # closing candle rallied vs. entry

    with archive.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM strategy_observations").fetchone()[0] == 0


def test_run_upstox_backtest_never_selects_an_angel_sourced_contract(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)

    # An Angel-sourced instrument with a strike CLOSER to spot than the Upstox one.
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2626101CE",
                "999",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                101,
            )
        ],
        start,
    )
    archive.save_candles(
        [Candle("NIFTY13AUG2626101CE", start + timedelta(minutes=10), 50, 55, 49, 52)],
        token="999",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=10),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    result = run_upstox_backtest(archive, strategy=strategy, settings=settings)

    assert result.trades == 1
    assert result.trade_details[0].token == "NSE_FO|1|13-08-2026"


def test_run_upstox_backtest_stop_risk_fraction_none_disables_the_stop(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    underlying = _underlying_candles(4, start=start)
    archive.save_upstox_candles(
        [
            UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close)
            for c in underlying
        ],
        token=NIFTY_UNDERLYING_KEY,
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=start,
    )
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2626600CE",
                "NSE_FO|1|13-08-2026",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                100,
            )
        ],
        start,
    )
    # A deep dip (low=50) that would normally trigger the stop immediately.
    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=10), 100, 101, 50, 92)],
        token="NSE_FO|1|13-08-2026",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=10),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    from options_bot.backtest import BacktestParameters

    result = run_upstox_backtest(
        archive,
        strategy=strategy,
        settings=settings,
        parameters=BacktestParameters(stop_risk_fraction=None),
    )

    trade = result.trade_details[0]
    assert trade.exit_reason not in ("stop", "stop-gap")
    assert trade.stop_price == 0.0


def test_run_upstox_backtest_respects_parameter_filters(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    from options_bot.backtest import BacktestParameters

    result = run_upstox_backtest(
        archive,
        strategy=strategy,
        settings=settings,
        parameters=BacktestParameters(name="ATR floor", minimum_atr=15),
    )

    assert result.status == "INSUFFICIENT DATA"
