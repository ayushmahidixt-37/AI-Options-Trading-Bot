from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from options_bot.candles import Candle
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive

IST = ZoneInfo("Asia/Kolkata")


def archive(tmp_path: Path) -> MarketArchive:
    result = MarketArchive(tmp_path / "market-data.sqlite3")
    result.initialize()
    return result


def test_candles_are_durable_and_duplicate_safe(tmp_path: Path) -> None:
    store = archive(tmp_path)
    started = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    candles = [Candle("Nifty 50", started, 100, 103, 99, 102)]

    assert store.save_candles(
        candles,
        token="99926000",
        exchange="NSE",
        timeframe="FIVE_MINUTE",
        collected_at=started + timedelta(minutes=5),
    ) == 1
    assert store.save_candles(
        candles,
        token="99926000",
        exchange="NSE",
        timeframe="FIVE_MINUTE",
        collected_at=started + timedelta(minutes=10),
    ) == 0

    reopened = MarketArchive(store.path)
    reopened.initialize()
    assert reopened.stats().candle_count == 1


def test_instrument_history_preserves_first_seen_and_updates_last_seen(tmp_path: Path) -> None:
    store = archive(tmp_path)
    first = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    later = first + timedelta(days=1)
    instrument = Instrument(
        "NIFTY13AUG2624600CE",
        "123",
        "NFO",
        "NIFTY",
        "CE",
        75,
        date(2026, 8, 13),
        24600,
    )

    store.save_instruments([instrument], first)
    store.save_instruments([instrument], later)

    with sqlite3.connect(store.path) as con:
        row = con.execute(
            "SELECT first_seen_at, last_seen_at FROM instruments WHERE token='123'"
        ).fetchone()
    assert row == (first.isoformat(), later.isoformat())
    assert store.nearest_expiry_summary(date(2026, 8, 6), 24620) == {
        "nearest_expiry": "2026-08-13",
        "atm_strike": 24600.0,
    }


def test_csv_export_and_sqlite_backup_are_readable(tmp_path: Path) -> None:
    store = archive(tmp_path)
    started = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    store.save_candles(
        [Candle("Nifty 50", started, 100, 103, 99, 102)],
        token="99926000",
        exchange="NSE",
        timeframe="FIVE_MINUTE",
        collected_at=started,
    )

    csv_path = store.export_candles_csv(tmp_path / "export.csv")
    backup_path = store.backup(tmp_path / "backup.sqlite3")

    assert "instrument_token,symbol" in csv_path.read_text(encoding="utf-8")
    with sqlite3.connect(backup_path) as con:
        assert con.execute("SELECT COUNT(*) FROM market_candles").fetchone()[0] == 1


def test_integrity_latest_candle_and_gap_summary(tmp_path: Path) -> None:
    store = archive(tmp_path)
    started = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    store.save_candles(
        [
            Candle("Nifty 50", started, 100, 103, 99, 102),
            Candle("Nifty 50", started + timedelta(minutes=10), 102, 104, 101, 103),
        ],
        token="99926000",
        exchange="NSE",
        timeframe="FIVE_MINUTE",
        collected_at=started + timedelta(minutes=10),
    )

    assert store.integrity_check() == "ok"
    assert store.latest_candle_at("99926000") == started + timedelta(minutes=10)
    assert store.gap_summary() == [
        {
            "token": "99926000",
            "symbol": "Nifty 50",
            "gaps": 1,
            "first_missing": (started + timedelta(minutes=5)).isoformat(),
            "last_missing": (started + timedelta(minutes=5)).isoformat(),
        }
    ]


def test_operational_state_and_backup_rotation_are_durable(tmp_path: Path) -> None:
    store = archive(tmp_path)
    now = datetime(2026, 8, 7, 10, 0, tzinfo=IST)
    store.set_operational_state("monitor_heartbeat", now.isoformat(), now)
    backup_dir = tmp_path / "backups"
    for day in range(4):
        store.backup(backup_dir / f"market-data-2026080{day + 1}.sqlite3")

    assert store.operational_state()["monitor_heartbeat"]["value"] == now.isoformat()
    assert store.rotate_backups(backup_dir, 2) == 2
    assert len(list(backup_dir.glob("*.sqlite3"))) == 2
