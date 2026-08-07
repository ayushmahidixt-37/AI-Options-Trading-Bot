from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from options_bot.candles import Candle
from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive
from options_bot.validation import export_validation_csv, run_strategy_validation

IST = ZoneInfo("Asia/Kolkata")


def _seed_day(archive: MarketArchive, day: date) -> None:
    signal_at = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(hour=10)
    archive.save_observation(
        signal_at,
        {
            "spot": 24600,
            "ema_fast": 24610,
            "ema_slow": 24590,
            "rsi_value": 58,
            "atr_value": 20,
            "signal_label": "BULLISH",
            "signal_confidence": 0.6,
            "signal_reason": "fixture",
            "data_status": "fresh",
        },
    )
    archive.save_candles(
        [
            Candle("NIFTY_TEST_CE", signal_at + timedelta(minutes=5), 100, 110, 99, 108),
            Candle("NIFTY_TEST_CE", signal_at + timedelta(minutes=10), 108, 112, 106, 110),
        ],
        token="CE1",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=signal_at + timedelta(minutes=15),
    )


def test_strategy_validation_uses_ordered_splits_and_tests_one_selection(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    archive.save_instruments(
        [
            Instrument(
                "NIFTY_TEST_CE",
                "CE1",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 9, 30),
                24600,
            )
        ],
        datetime(2026, 8, 1, tzinfo=IST),
    )
    for day in (date(2026, 8, 3), date(2026, 8, 10), date(2026, 8, 17)):
        _seed_day(archive, day)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )

    report = run_strategy_validation(
        archive,
        settings,
        development_start=date(2026, 8, 3),
        development_end=date(2026, 8, 3),
        validation_start=date(2026, 8, 10),
        validation_end=date(2026, 8, 10),
        test_start=date(2026, 8, 17),
        test_end=date(2026, 8, 17),
    )

    assert report.selected_name
    assert sum(row.test is not None for row in report.rows) == 1
    assert next(row.test for row in report.rows if row.test is not None).trades == 1
    exported = export_validation_csv(report, tmp_path / "comparison.csv")
    assert "untouched" not in exported.read_text(encoding="utf-8").lower()
    assert "validation_net_pnl" in exported.read_text(encoding="utf-8")


def test_strategy_validation_rejects_overlapping_ranges(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    with pytest.raises(ValueError, match="non-overlapping"):
        run_strategy_validation(
            archive,
            settings,
            development_start=date(2026, 8, 1),
            development_end=date(2026, 8, 10),
            validation_start=date(2026, 8, 10),
            validation_end=date(2026, 8, 15),
            test_start=date(2026, 8, 16),
            test_end=date(2026, 8, 20),
        )
