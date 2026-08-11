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
from .upstox_data import UpstoxCandle


@dataclass(frozen=True)
class ArchiveStats:
    candle_count: int
    nifty_candle_count: int
    option_candle_count: int
    instrument_count: int
    oldest_candle_at: str | None
    newest_candle_at: str | None
    database_bytes: int
    missing_five_minute_buckets: int
    integrity_status: str = "unknown"


@dataclass(frozen=True)
class ReadinessMetrics:
    trading_days: int
    observation_count: int
    successful_runs: int
    failed_runs: int


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
                CREATE TABLE IF NOT EXISTS operational_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS candle_time_idx ON market_candles(started_at);
                CREATE INDEX IF NOT EXISTS instrument_expiry_idx
                    ON instruments(underlying, expiry, strike);
                """
            )
            self._ensure_column(con, "market_candles", "open_interest", "REAL")

    @staticmethod
    def _ensure_column(con: sqlite3.Connection, table: str, column: str, sql_type: str) -> None:
        """Add a nullable column if missing (SQLite has no ADD COLUMN IF NOT EXISTS)."""
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def set_operational_state(
        self, key: str, value: object, observed_at: datetime
    ) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO operational_state(key, value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                (key, str(value), observed_at.isoformat()),
            )

    def operational_state(self) -> dict[str, dict[str, str]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT key, value, updated_at FROM operational_state"
            ).fetchall()
        return {
            str(row[0]): {"value": str(row[1]), "updated_at": str(row[2])}
            for row in rows
        }

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

    def save_upstox_candles(
        self,
        candles: list[UpstoxCandle],
        *,
        token: str,
        exchange: str,
        timeframe: str,
        collected_at: datetime,
    ) -> int:
        """Store Upstox-sourced candles under ``source='upstox'``.

        Kept separate from ``save_candles`` (the always-running Angel path)
        so this read-only backtesting feature can never change Angel
        ingestion behavior.
        """
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
                "upstox",
                collected_at.isoformat(),
                candle.open_interest,
            )
            for candle in candles
        ]
        with self.connect() as con:
            before = con.total_changes
            con.executemany(
                """INSERT OR IGNORE INTO market_candles(
                       instrument_token, symbol, exchange_name, timeframe, started_at,
                       open, high, low, close, source, collected_at, open_interest
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            nifty_count = con.execute(
                "SELECT COUNT(*) FROM market_candles WHERE instrument_token='99926000'"
            ).fetchone()[0]
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
            nifty_candle_count=int(nifty_count),
            option_candle_count=int(candle[0]) - int(nifty_count),
            instrument_count=int(instruments),
            oldest_candle_at=candle[1],
            newest_candle_at=candle[2],
            database_bytes=self.path.stat().st_size if self.path.exists() else 0,
            missing_five_minute_buckets=missing,
            integrity_status=self.integrity_check(),
        )

    def integrity_check(self) -> str:
        """Run SQLite's lightweight integrity check and return a display value."""
        with self.connect() as con:
            row = con.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def readiness_metrics(self) -> ReadinessMetrics:
        with self.connect() as con:
            row = con.execute(
                """SELECT COUNT(DISTINCT date(started_at)), COUNT(*)
                   FROM market_candles WHERE instrument_token='99926000'"""
            ).fetchone()
            observations = int(
                con.execute("SELECT COUNT(*) FROM strategy_observations").fetchone()[0]
            )
            runs = con.execute(
                """SELECT
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END)
                   FROM collection_runs"""
            ).fetchone()
        return ReadinessMetrics(
            trading_days=int(row[0] or 0),
            observation_count=observations,
            successful_runs=int(runs[0] or 0),
            failed_runs=int(runs[1] or 0),
        )

    def latest_candle_at(self, token: str, timeframe: str = "FIVE_MINUTE") -> datetime | None:
        with self.connect() as con:
            row = con.execute(
                """SELECT MAX(started_at) FROM market_candles
                   WHERE instrument_token=? AND timeframe=?""",
                (token, timeframe),
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def gap_summary(self) -> list[dict[str, object]]:
        """Describe internal same-session gaps grouped by archived instrument."""
        with self.connect() as con:
            rows = con.execute(
                """SELECT instrument_token, symbol, started_at FROM market_candles
                   WHERE timeframe='FIVE_MINUTE'
                   ORDER BY instrument_token, started_at"""
            ).fetchall()
        grouped: dict[tuple[str, str], list[datetime]] = {}
        for row in rows:
            grouped.setdefault((row[0], row[1]), []).append(datetime.fromisoformat(row[2]))
        result: list[dict[str, object]] = []
        for (token, symbol), timestamps in grouped.items():
            gaps = [
                (previous + timedelta(minutes=5), current - timedelta(minutes=5))
                for previous, current in zip(timestamps, timestamps[1:])
                if previous.date() == current.date()
                and current - previous > timedelta(minutes=5)
            ]
            if gaps:
                result.append(
                    {
                        "token": token,
                        "symbol": symbol,
                        "gaps": len(gaps),
                        "first_missing": gaps[0][0].isoformat(),
                        "last_missing": gaps[-1][1].isoformat(),
                    }
                )
        return result

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

    def select_near_atm_options(
        self, today: date, spot: float, strike_band: int = 5
    ) -> list[Instrument]:
        if spot <= 0 or strike_band < 0:
            raise ValueError("spot must be positive and strike band non-negative")
        with self.connect() as con:
            expiry = con.execute(
                """SELECT MIN(expiry) FROM instruments
                   WHERE underlying='NIFTY' AND expiry>=?""",
                (today.isoformat(),),
            ).fetchone()[0]
            if not expiry:
                return []
            strikes = [
                float(row[0])
                for row in con.execute(
                    """SELECT DISTINCT strike FROM instruments
                       WHERE underlying='NIFTY' AND expiry=? ORDER BY strike""",
                    (expiry,),
                )
            ]
            if not strikes:
                return []
            atm = min(range(len(strikes)), key=lambda index: abs(strikes[index] - spot))
            selected = strikes[max(0, atm - strike_band) : atm + strike_band + 1]
            placeholders = ",".join("?" for _ in selected)
            rows = con.execute(
                f"""SELECT symbol, token, exchange_name, underlying, option_type,
                           lot_size, expiry, strike
                    FROM instruments WHERE underlying='NIFTY' AND expiry=?
                    AND strike IN ({placeholders}) ORDER BY strike, option_type""",
                (expiry, *selected),
            ).fetchall()
        return [
            Instrument(
                symbol=row[0],
                token=row[1],
                exchange=row[2],
                underlying=row[3],
                option_type=row[4],
                lot_size=int(row[5]),
                expiry=date.fromisoformat(row[6]),
                strike=float(row[7]),
            )
            for row in rows
        ]

    def has_upstox_candles(self, token: str, start: date, end: date) -> bool:
        """Whether any Upstox-sourced candle already exists for a token in [start, end].

        Used to skip a redundant Upstox API call for data already archived,
        not to prove the range is gap-free — that's a coarser, cheaper check
        deliberately traded off against always re-fetching.
        """
        with self.connect() as con:
            row = con.execute(
                """SELECT 1 FROM market_candles
                   WHERE instrument_token=? AND source='upstox'
                     AND date(started_at)>=? AND date(started_at)<=? LIMIT 1""",
                (token, start.isoformat(), end.isoformat()),
            ).fetchone()
        return row is not None

    def upstox_coverage_ranges(self, underlying_key: str) -> list[tuple[date, date]]:
        """Contiguous date ranges already archived for the Upstox underlying candles.

        Used to show what's already backtestable without pulling anything new.
        """
        with self.connect() as con:
            rows = con.execute(
                """SELECT DISTINCT date(started_at) FROM market_candles
                   WHERE instrument_token=? AND source='upstox' ORDER BY date(started_at)""",
                (underlying_key,),
            ).fetchall()
        days = [date.fromisoformat(row[0]) for row in rows]
        ranges: list[tuple[date, date]] = []
        range_start: date | None = None
        previous: date | None = None
        for day in days:
            if range_start is None:
                range_start = day
            elif (day - previous).days > 1:
                ranges.append((range_start, previous))
                range_start = day
            previous = day
        if range_start is not None and previous is not None:
            ranges.append((range_start, previous))
        return ranges

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

    def rotate_backups(self, directory: str | Path, keep: int) -> int:
        if keep < 1:
            raise ValueError("keep must be positive")
        folder = Path(directory)
        backups = sorted(folder.glob("market-data-*.sqlite3"), reverse=True)
        removed = 0
        for path in backups[keep:]:
            path.unlink(missing_ok=True)
            removed += 1
        return removed
