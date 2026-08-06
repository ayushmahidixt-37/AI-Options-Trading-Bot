"""Durable, local SQLite archive for read-only market data and backtesting."""

from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from .candles import Candle
from .domain import Instrument


@dataclass(frozen=True)
class ArchiveStats:
    candle_count: int
    instrument_count: int
    oldest_candle_at: str | None
    newest_candle_at: str | None
    database_bytes: int
    missing_five_minute_buckets: int


class MarketArchive:
    """Own a transaction-safe database separate from the paper-order ledger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS market_candles (
                    instrument_token TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exchange_name TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    source TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    PRIMARY KEY(instrument_token, timeframe, started_at)
                );
                CREATE TABLE IF NOT EXISTS instruments (
                    token TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    exchange_name TEXT NOT NULL,
                    underlying TEXT NOT NULL,
                    option_type TEXT NOT NULL CHECK(option_type IN ('CE','PE')),
                    strike REAL NOT NULL,
                    expiry TEXT NOT NULL,
                    lot_size INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_observations (
                    observed_at TEXT PRIMARY KEY,
                    strategy_version TEXT NOT NULL,
                    spot REAL,
                    ema_fast REAL,
                    ema_slow REAL,
                    rsi REAL,
                    atr REAL,
                    signal TEXT NOT NULL,
                    confidence REAL,
                    reason TEXT NOT NULL,
                    data_status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    saved_candles INTEGER NOT NULL,
                    saved_instruments INTEGER NOT NULL,
                    details TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS candle_time_idx ON market_candles(started_at);
                CREATE INDEX IF NOT EXISTS instrument_expiry_idx
                    ON instruments(underlying, expiry, strike);
                """
            )

    def save_candles(
        self,
        candles: list[Candle],
        *,
        token: str,
        exchange: str,
        timeframe: str,
        collected_at: datetime,
    ) -> int:
        rows = [
            (
                token,
                candle.symbol,
                exchange,
                timeframe,
                candle.started_at.isoformat(),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                "angel-one",
                collected_at.isoformat(),
            )
            for candle in candles
        ]
        with self.connect() as con:
            before = con.total_changes
            con.executemany(
                """INSERT OR IGNORE INTO market_candles(
                       instrument_token, symbol, exchange_name, timeframe, started_at,
                       open, high, low, close, source, collected_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            return con.total_changes - before

    def save_instruments(self, instruments: list[Instrument], observed_at: datetime) -> int:
        rows = [
            (
                item.token,
                item.symbol,
                item.exchange,
                item.underlying,
                item.option_type,
                item.strike,
                item.expiry.isoformat(),
                item.lot_size,
                observed_at.isoformat(),
                observed_at.isoformat(),
            )
            for item in instruments
            if item.token and item.expiry and item.strike is not None and item.lot_size > 0
        ]
        with self.connect() as con:
            con.executemany(
                """INSERT INTO instruments(
                       token, symbol, exchange_name, underlying, option_type, strike,
                       expiry, lot_size, first_seen_at, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(token) DO UPDATE SET
                       symbol=excluded.symbol, exchange_name=excluded.exchange_name,
                       underlying=excluded.underlying, option_type=excluded.option_type,
                       strike=excluded.strike, expiry=excluded.expiry,
                       lot_size=excluded.lot_size, last_seen_at=excluded.last_seen_at""",
                rows,
            )
        return len(rows)

    def save_observation(self, observed_at: datetime, values: dict[str, object]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO strategy_observations(
                       observed_at, strategy_version, spot, ema_fast, ema_slow, rsi, atr,
                       signal, confidence, reason, data_status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observed_at.isoformat(),
                    "momentum-v1",
                    values.get("spot"),
                    values.get("ema_fast"),
                    values.get("ema_slow"),
                    values.get("rsi_value"),
                    values.get("atr_value"),
                    values["signal_label"],
                    values.get("signal_confidence"),
                    values["signal_reason"],
                    values["data_status"],
                ),
            )

    def record_run(
        self, observed_at: datetime, status: str, candles: int, instruments: int, details: str
    ) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO collection_runs(
                       occurred_at, status, saved_candles, saved_instruments, details
                   ) VALUES (?, ?, ?, ?, ?)""",
                (observed_at.isoformat(), status, candles, instruments, details),
            )

    def stats(self) -> ArchiveStats:
        with self.connect() as con:
            candle = con.execute(
                "SELECT COUNT(*), MIN(started_at), MAX(started_at) FROM market_candles"
            ).fetchone()
            instruments = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
            timestamps = [
                datetime.fromisoformat(row[0])
                for row in con.execute(
                    """SELECT started_at FROM market_candles
                       WHERE instrument_token='99926000' AND timeframe='FIVE_MINUTE'
                       ORDER BY started_at"""
                )
            ]
        missing = sum(
            max(0, int((current - previous) / timedelta(minutes=5)) - 1)
            for previous, current in zip(timestamps, timestamps[1:])
            if previous.date() == current.date()
        )
        return ArchiveStats(
            candle_count=int(candle[0]),
            instrument_count=int(instruments),
            oldest_candle_at=candle[1],
            newest_candle_at=candle[2],
            database_bytes=self.path.stat().st_size if self.path.exists() else 0,
            missing_five_minute_buckets=missing,
        )

    def nearest_expiry_summary(self, today: date, spot: float | None) -> dict[str, object]:
        with self.connect() as con:
            expiry_row = con.execute(
                """SELECT MIN(expiry) FROM instruments
                   WHERE underlying='NIFTY' AND expiry>=?""",
                (today.isoformat(),),
            ).fetchone()
            expiry = expiry_row[0]
            if not expiry or not spot:
                return {"nearest_expiry": expiry, "atm_strike": None}
            strike = con.execute(
                """SELECT strike FROM instruments
                   WHERE underlying='NIFTY' AND expiry=?
                   ORDER BY ABS(strike-?) LIMIT 1""",
                (expiry, spot),
            ).fetchone()
        return {"nearest_expiry": expiry, "atm_strike": strike[0] if strike else None}

    def export_candles_csv(self, target: str | Path) -> Path:
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con, destination.open("w", newline="", encoding="utf-8") as handle:
            rows = con.execute("SELECT * FROM market_candles ORDER BY started_at")
            writer = csv.writer(handle)
            writer.writerow(column[0] for column in rows.description)
            writer.writerows(rows)
        return destination

    def backup(self, target: str | Path) -> Path:
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as backup:
            source.backup(backup)
        return destination
