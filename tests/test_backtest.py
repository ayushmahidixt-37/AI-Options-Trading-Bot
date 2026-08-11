from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.backtest import BacktestParameters, export_backtest_csv, run_momentum_backtest
from options_bot.candles import Candle
from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive

IST = ZoneInfo("Asia/Kolkata")


def observation(signal: str) -> dict[str, object]:
    return {
        "spot": 24600,
        "ema_fast": 2,
        "ema_slow": 1,
        "rsi_value": 55,
        "atr_value": 20,
        "signal_label": signal,
        "signal_confidence": 0.55,
        "signal_reason": "fixture",
        "data_status": "fresh",
    }


def test_offline_backtest_uses_next_option_open_without_network(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2624600CE",
                "CE1",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                24600,
            ),
            Instrument(
                "NIFTY13AUG2624600PE",
                "PE1",
                "NFO",
                "NIFTY",
                "PE",
                75,
                date(2026, 8, 13),
                24600,
            ),
        ],
        start,
    )
    archive.save_observation(start, observation("BULLISH"))
    archive.save_observation(start + timedelta(minutes=15), observation("BEARISH"))
    archive.save_candles(
        [
            Candle("NIFTY13AUG2624600CE", start + timedelta(minutes=5), 100, 108, 99, 106),
            Candle("NIFTY13AUG2624600CE", start + timedelta(minutes=10), 106, 109, 104, 105),
        ],
        token="CE1",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=15),
    )

    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        }
    )
    result = run_momentum_backtest(archive, settings=settings)

    assert result.status == "PRELIMINARY"
    assert result.trades == 1
    assert result.winners == 1
    assert result.gross_pnl_points == 5
    assert result.net_pnl == 296.75
    assert result.fees_paid == 40
    assert result.max_drawdown == 0
    report = export_backtest_csv(result, tmp_path / "trades.csv")
    assert "exit_reason" in report.read_text(encoding="utf-8")
    assert "signal-reversal" in report.read_text(encoding="utf-8")


def test_backtest_reports_insufficient_archive(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()

    assert run_momentum_backtest(archive).status == "INSUFFICIENT DATA"


def test_backtest_uses_conservative_stop_fill(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2624600CE",
                "CE1",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                24600,
            )
        ],
        start,
    )
    archive.save_observation(start, observation("BULLISH"))
    archive.save_candles(
        [Candle("NIFTY13AUG2624600CE", start + timedelta(minutes=5), 100, 101, 90, 92)],
        token="CE1",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=10),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )

    result = run_momentum_backtest(archive, settings=settings)

    assert result.trade_details[0].exit_reason == "stop"
    assert result.trade_details[0].exit_price < result.trade_details[0].entry_price


def test_stop_risk_fraction_none_disables_the_price_based_stop(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2624600CE",
                "CE1",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                24600,
            )
        ],
        start,
    )
    archive.save_observation(start, observation("BULLISH"))
    # Same candle as test_backtest_uses_conservative_stop_fill: low=90 would
    # normally trigger the stop well before the session ends.
    archive.save_candles(
        [Candle("NIFTY13AUG2624600CE", start + timedelta(minutes=5), 100, 101, 90, 92)],
        token="CE1",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=10),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )

    result = run_momentum_backtest(
        archive, settings=settings, parameters=BacktestParameters(stop_risk_fraction=None)
    )

    trade = result.trade_details[0]
    assert trade.exit_reason != "stop"
    assert trade.exit_reason != "stop-gap"
    assert trade.stop_price == 0.0
