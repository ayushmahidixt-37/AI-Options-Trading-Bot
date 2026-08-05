"""Transactional SQLite persistence for paper orders and positions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1


class PaperLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
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
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    starting_capital REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    fees_paid REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL UNIQUE,
                    trading_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    closed_at TEXT,
                    symbol TEXT NOT NULL,
                    token TEXT NOT NULL,
                    exchange_name TEXT NOT NULL,
                    underlying TEXT NOT NULL,
                    option_type TEXT NOT NULL CHECK(option_type IN ('CE','PE')),
                    side TEXT NOT NULL CHECK(side = 'BUY'),
                    lots INTEGER NOT NULL CHECK(lots > 0),
                    lot_size INTEGER NOT NULL CHECK(lot_size > 0),
                    units INTEGER NOT NULL CHECK(units = lots * lot_size),
                    requested_price REAL NOT NULL,
                    entry_fill_price REAL NOT NULL,
                    exit_fill_price REAL,
                    stop_price REAL NOT NULL,
                    entry_fee REAL NOT NULL,
                    exit_fee REAL NOT NULL DEFAULT 0,
                    realized_pnl REAL,
                    status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')),
                    strategy TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    close_reason TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_symbol
                    ON paper_orders(symbol) WHERE status='OPEN';
                CREATE TABLE IF NOT EXISTS bot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL
                );
                """
            )
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )

    def create_account(self, starting_capital: float) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO paper_account(id, starting_capital) VALUES (1, ?)",
                (starting_capital,),
            )

    def account(self) -> sqlite3.Row:
        with self.connect() as con:
            row = con.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("Paper account is not initialized")
        return row

    def open_positions(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return list(con.execute("SELECT * FROM paper_orders WHERE status='OPEN' ORDER BY id"))

    def trades_on(self, trading_date: str) -> int:
        with self.connect() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE trading_date=?", (trading_date,)
            ).fetchone()
        return int(row[0])

    def realized_pnl_on(self, trading_date: str) -> float:
        with self.connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM paper_orders WHERE trading_date=? AND status='CLOSED'",
                (trading_date,),
            ).fetchone()
        return float(row[0])

    def insert_open(self, values: dict[str, object]) -> int:
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        with self.connect() as con:
            cursor = con.execute(
                f"INSERT INTO paper_orders({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            return int(cursor.lastrowid)

    def insert_open_with_fee(self, values: dict[str, object], fee: float) -> int:
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        with self.connect() as con:
            cursor = con.execute(
                f"INSERT INTO paper_orders({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            con.execute("UPDATE paper_account SET fees_paid=fees_paid+? WHERE id=1", (fee,))
            return int(cursor.lastrowid)

    def close(self, order_id: int, *, at: str, price: float, fee: float, pnl: float, reason: str) -> None:
        with self.connect() as con:
            cursor = con.execute(
                """UPDATE paper_orders
                   SET status='CLOSED', closed_at=?, exit_fill_price=?, exit_fee=?,
                       realized_pnl=?, close_reason=?
                   WHERE id=? AND status='OPEN'""",
                (at, price, fee, pnl, reason, order_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Paper position is not open")
            con.execute(
                "UPDATE paper_account SET realized_pnl=realized_pnl+?, fees_paid=fees_paid+? WHERE id=1",
                (pnl, fee),
            )
