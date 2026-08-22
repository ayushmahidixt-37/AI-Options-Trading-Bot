from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from options_bot.candles import Candle
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive
from options_bot.upstox_data import UpstoxCandle

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


def test_gap_summary_never_mixes_angel_and_upstox_sources(tmp_path: Path) -> None:
    store = archive(tmp_path)
    started = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    # A real gap in the Angel-sourced candles only.
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
    # A continuous, gap-free run of Upstox-sourced candles for a different token.
    store.save_upstox_candles(
        [
            UpstoxCandle("NIFTY", started, 100, 103, 99, 102),
            UpstoxCandle("NIFTY", started + timedelta(minutes=5), 102, 104, 101, 103),
        ],
        token="NSE_INDEX|Nifty 50",
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=started + timedelta(minutes=5),
    )

    assert len(store.gap_summary("angel-one")) == 1
    assert store.gap_summary("upstox") == []
    assert store.gap_summary() == store.gap_summary("angel-one")


def test_initialize_is_idempotent_and_adds_open_interest_column(tmp_path: Path) -> None:
    store = archive(tmp_path)
    store.initialize()  # run twice: migration must not fail on an existing column

    with sqlite3.connect(store.path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(market_candles)")}
    assert "open_interest" in columns


def test_upstox_candles_are_duplicate_safe_and_carry_open_interest(tmp_path: Path) -> None:
    store = archive(tmp_path)
    started = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    candles = [UpstoxCandle("NIFTY24APR25000CE", started, 100, 103, 99, 102, open_interest=5000)]

    assert store.save_upstox_candles(
        candles,
        token="NSE_FO|53806|24-04-2025",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=started,
    ) == 1
    assert store.save_upstox_candles(
        candles,
        token="NSE_FO|53806|24-04-2025",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=started,
    ) == 0

    with sqlite3.connect(store.path) as con:
        row = con.execute(
            "SELECT source, open_interest FROM market_candles WHERE instrument_token=?",
            ("NSE_FO|53806|24-04-2025",),
        ).fetchone()
    assert row == ("upstox", 5000.0)


def test_upstox_candles_default_to_not_derived_and_can_be_tagged(tmp_path: Path) -> None:
    """A resampled/materialized bar must never be indistinguishable from a
    real, directly-fetched one -- see BACKTEST_FINDINGS.md's 2026-08-21
    data-integrity entry, where exactly that silently broke reproducibility."""
    store = archive(tmp_path)
    started = datetime(2026, 8, 6, 9, 15, tzinfo=IST)

    store.save_upstox_candles(
        [UpstoxCandle("NIFTY24APR25000CE", started, 100, 103, 99, 102)],
        token="NSE_FO|real|24-04-2025", exchange="NFO",
        timeframe="FIVE_MINUTE", collected_at=started,
    )
    store.save_upstox_candles(
        [UpstoxCandle("NIFTY24APR25000CE", started, 100, 103, 99, 102)],
        token="NSE_FO|derived|24-04-2025", exchange="NFO",
        timeframe="FIVE_MINUTE", collected_at=started,
        derived_from_timeframe="ONE_MINUTE",
    )

    with sqlite3.connect(store.path) as con:
        rows = dict(
            con.execute(
                "SELECT instrument_token, derived_from_timeframe FROM market_candles "
                "WHERE instrument_token IN (?, ?)",
                ("NSE_FO|real|24-04-2025", "NSE_FO|derived|24-04-2025"),
            ).fetchall()
        )
    assert rows["NSE_FO|real|24-04-2025"] is None
    assert rows["NSE_FO|derived|24-04-2025"] == "ONE_MINUTE"


def test_upstox_and_angel_candles_coexist_without_collision(tmp_path: Path) -> None:
    store = archive(tmp_path)
    started = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    store.save_candles(
        [Candle("Nifty 50", started, 100, 103, 99, 102)],
        token="99926000",
        exchange="NSE",
        timeframe="FIVE_MINUTE",
        collected_at=started,
    )
    store.save_upstox_candles(
        [UpstoxCandle("Nifty 50", started, 100, 103, 99, 102)],
        token="NSE_INDEX|Nifty 50",
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=started,
    )

    stats = store.stats()
    assert stats.candle_count == 2
    with sqlite3.connect(store.path) as con:
        sources = {
            row[0]
            for row in con.execute("SELECT source FROM market_candles ORDER BY source")
        }
    assert sources == {"angel-one", "upstox"}


def test_has_upstox_candles_reports_presence_by_token_and_range(tmp_path: Path) -> None:
    store = archive(tmp_path)
    started = datetime(2026, 7, 3, 9, 15, tzinfo=IST)
    store.save_upstox_candles(
        [UpstoxCandle("Nifty 50", started, 100, 103, 99, 102)],
        token="NSE_INDEX|Nifty 50",
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=started,
    )

    assert store.has_upstox_candles("NSE_INDEX|Nifty 50", date(2026, 7, 1), date(2026, 7, 7), "FIVE_MINUTE")
    assert not store.has_upstox_candles("NSE_INDEX|Nifty 50", date(2026, 6, 1), date(2026, 6, 30), "FIVE_MINUTE")
    assert not store.has_upstox_candles("NSE_FO|999|31-12-2026", date(2026, 7, 1), date(2026, 7, 7), "FIVE_MINUTE")


def test_has_upstox_candles_does_not_cross_timeframes(tmp_path: Path) -> None:
    """Regression test: existing FIVE_MINUTE data must never make a
    ONE_MINUTE presence check for the same token/range return True -- this
    exact bug caused a real ingestion gap (see BACKTEST_FINDINGS.md's
    2026-08-21 multi-timeframe entry): a one-minute pull silently skipped
    every date range that already had five-minute data for the same token.
    """
    store = archive(tmp_path)
    started = datetime(2026, 7, 3, 9, 15, tzinfo=IST)
    store.save_upstox_candles(
        [UpstoxCandle("Nifty 50", started, 100, 103, 99, 102)],
        token="NSE_INDEX|Nifty 50",
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=started,
    )

    assert store.has_upstox_candles("NSE_INDEX|Nifty 50", date(2026, 7, 1), date(2026, 7, 7), "FIVE_MINUTE")
    assert not store.has_upstox_candles("NSE_INDEX|Nifty 50", date(2026, 7, 1), date(2026, 7, 7), "ONE_MINUTE")


def test_upstox_coverage_ranges_groups_contiguous_days(tmp_path: Path) -> None:
    store = archive(tmp_path)
    token = "NSE_INDEX|Nifty 50"

    def seed(day: date) -> None:
        started = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
        store.save_upstox_candles(
            [UpstoxCandle("Nifty 50", started, 100, 103, 99, 102)],
            token=token,
            exchange="NSE_INDEX",
            timeframe="FIVE_MINUTE",
            collected_at=started,
        )

    for day in (date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)):
        seed(day)
    for day in (date(2026, 7, 10), date(2026, 7, 11)):
        seed(day)

    assert store.upstox_coverage_ranges(token) == [
        (date(2026, 7, 1), date(2026, 7, 3)),
        (date(2026, 7, 10), date(2026, 7, 11)),
    ]


def test_upstox_coverage_ranges_empty_when_nothing_archived(tmp_path: Path) -> None:
    store = archive(tmp_path)
    assert store.upstox_coverage_ranges("NSE_INDEX|Nifty 50") == []


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
