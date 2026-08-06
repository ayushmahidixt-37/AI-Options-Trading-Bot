from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.backtest import run_momentum_backtest
from options_bot.candles import Candle
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

    result = run_momentum_backtest(archive)

    assert result.status == "READY"
    assert result.trades == 1
    assert result.winners == 1
    assert result.gross_pnl_points == 5


def test_backtest_reports_insufficient_archive(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()

    assert run_momentum_backtest(archive).status == "INSUFFICIENT DATA"
