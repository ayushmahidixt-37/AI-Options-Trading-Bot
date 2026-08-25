"""Transactional SQLite persistence for paper orders and positions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2


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
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    trade_group_id TEXT,
                    lots INTEGER NOT NULL CHECK(lots > 0),
                    lot_size INTEGER NOT NULL CHECK(lot_size > 0),
                    units INTEGER NOT NULL CHECK(units = lots * lot_size),
                    requested_price REAL NOT NULL,
                    entry_fill_price REAL NOT NULL,
                    exit_fill_price REAL,
                    stop_price REAL NOT NULL,
                    target_price REAL,
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
                CREATE TABLE IF NOT EXISTS paper_trade_journal (
                    order_id INTEGER PRIMARY KEY REFERENCES paper_orders(id),
                    signal_at TEXT,
                    direction TEXT NOT NULL,
                    nifty_spot REAL,
                    ema_fast REAL,
                    ema_slow REAL,
                    rsi_value REAL,
                    atr_value REAL,
                    confidence REAL,
                    expiry TEXT,
                    strike REAL,
                    estimated_max_loss REAL,
                    max_favorable_points REAL NOT NULL DEFAULT 0,
                    max_adverse_points REAL NOT NULL DEFAULT 0,
                    data_warning TEXT
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )
            self._ensure_column(con, "paper_orders", "target_price", "REAL")
            self._migrate_side_check_constraint(con)
            self._ensure_column(con, "paper_orders", "trade_group_id", "TEXT")

    @staticmethod
    def _ensure_column(con: sqlite3.Connection, table: str, column: str, sql_type: str) -> None:
        """Add a nullable column if missing (SQLite has no ADD COLUMN IF NOT EXISTS).

        Added 2026-08-24 alongside ``target_price`` -- an existing
        ``paper.sqlite3`` created before profit-target exits were wired in
        won't have this column from ``CREATE TABLE IF NOT EXISTS`` alone.
        """
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    @staticmethod
    def _migrate_side_check_constraint(con: sqlite3.Connection) -> None:
        """Relax ``paper_orders.side``'s CHECK from BUY-only to BUY/SELL.

        Added 2026-08-25 for short-strangle live support (selling option
        legs). SQLite has no ALTER TABLE for CHECK constraints -- an
        existing table created under the old, stricter constraint needs a
        real rebuild (new table, copy rows, swap in), done here defensively
        so any already-recorded real paper trades are preserved, not lost.
        A no-op (checked via the stored CREATE TABLE SQL) once already
        migrated, so this is safe to run on every startup.
        """
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='paper_orders'"
        ).fetchone()
        if row is None or "CHECK(side = 'BUY')" not in row[0]:
            return
        con.executescript(
            """
            PRAGMA foreign_keys=OFF;
            ALTER TABLE paper_orders RENAME TO paper_orders_pre_sell_migration;
            CREATE TABLE paper_orders (
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
                side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                trade_group_id TEXT,
                lots INTEGER NOT NULL CHECK(lots > 0),
                lot_size INTEGER NOT NULL CHECK(lot_size > 0),
                units INTEGER NOT NULL CHECK(units = lots * lot_size),
                requested_price REAL NOT NULL,
                entry_fill_price REAL NOT NULL,
                exit_fill_price REAL,
                stop_price REAL NOT NULL,
                target_price REAL,
                entry_fee REAL NOT NULL,
                exit_fee REAL NOT NULL DEFAULT 0,
                realized_pnl REAL,
                status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')),
                strategy TEXT NOT NULL,
                reason TEXT NOT NULL,
                close_reason TEXT
            );
            INSERT INTO paper_orders (
                id, client_order_id, trading_date, created_at, closed_at, symbol, token,
                exchange_name, underlying, option_type, side, lots, lot_size, units,
                requested_price, entry_fill_price, exit_fill_price, stop_price, target_price,
                entry_fee, exit_fee, realized_pnl, status, strategy, reason, close_reason
            )
            SELECT
                id, client_order_id, trading_date, created_at, closed_at, symbol, token,
                exchange_name, underlying, option_type, side, lots, lot_size, units,
                requested_price, entry_fill_price, exit_fill_price, stop_price, target_price,
                entry_fee, exit_fee, realized_pnl, status, strategy, reason, close_reason
            FROM paper_orders_pre_sell_migration;
            DROP TABLE paper_orders_pre_sell_migration;
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_symbol
                ON paper_orders(symbol) WHERE status='OPEN';
            PRAGMA foreign_keys=ON;
            """
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

    def capital_summary(self) -> dict[str, float | int]:
        """Return paper capital committed to open positions and still available."""
        account = self.account()
        positions = self.open_positions()
        premium_committed = sum(
            float(row["entry_fill_price"]) * int(row["units"]) for row in positions
        )
        open_entry_fees = sum(float(row["entry_fee"]) for row in positions)
        capital_used = premium_committed + open_entry_fees
        capital_before_open_pnl = (
            float(account["starting_capital"]) + float(account["realized_pnl"])
        )
        return {
            "premium_committed": round(premium_committed, 2),
            "open_entry_fees": round(open_entry_fees, 2),
            "capital_used": round(capital_used, 2),
            "capital_available": round(capital_before_open_pnl - capital_used, 2),
            "open_positions": len(positions),
        }

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

    def daily_summary(self, trading_date: str) -> dict[str, object]:
        with self.connect() as con:
            row = con.execute(
                """SELECT COUNT(*) AS trades,
                          SUM(CASE WHEN status='CLOSED' AND realized_pnl>0 THEN 1 ELSE 0 END) AS wins,
                          SUM(CASE WHEN status='CLOSED' AND realized_pnl<=0 THEN 1 ELSE 0 END) AS losses,
                          COALESCE(SUM(CASE WHEN status='CLOSED' THEN realized_pnl ELSE 0 END), 0) AS net_pnl,
                          COALESCE(SUM(entry_fee + exit_fee), 0) AS fees,
                          SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_positions
                   FROM paper_orders WHERE trading_date=?""",
                (trading_date,),
            ).fetchone()
        return {key: row[key] or 0 for key in row.keys()}

    def record_event(self, occurred_at: str, level: str, event_type: str, details: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO bot_events(occurred_at, level, event_type, details) VALUES (?, ?, ?, ?)",
                (occurred_at, level, event_type, details),
            )

    def recent_events(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as con:
            return list(
                con.execute(
                    "SELECT * FROM bot_events ORDER BY id DESC LIMIT ?", (max(1, limit),)
                )
            )

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO runtime_state(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_state(self, key: str, default: str = "") -> str:
        with self.connect() as con:
            row = con.execute("SELECT value FROM runtime_state WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def record_trade_journal(self, order_id: int, values: dict[str, object]) -> None:
        columns = ["order_id", *values]
        placeholders = ",".join("?" for _ in columns)
        with self.connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO paper_trade_journal({','.join(columns)}) "
                f"VALUES ({placeholders})",
                (order_id, *values.values()),
            )

    def update_trade_excursion(self, order_id: int, option_points: float) -> None:
        with self.connect() as con:
            con.execute(
                """UPDATE paper_trade_journal
                   SET max_favorable_points=MAX(max_favorable_points, ?),
                       max_adverse_points=MAX(max_adverse_points, ?)
                   WHERE order_id=?""",
                (max(0.0, option_points), max(0.0, -option_points), order_id),
            )

    def trade_journal(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as con:
            return list(
                con.execute(
                    """SELECT o.*, j.signal_at, j.direction, j.nifty_spot,
                              j.ema_fast, j.ema_slow, j.rsi_value, j.atr_value,
                              j.confidence, j.expiry, j.strike,
                              j.estimated_max_loss, j.max_favorable_points,
                              j.max_adverse_points, j.data_warning
                       FROM paper_orders o JOIN paper_trade_journal j ON j.order_id=o.id
                       ORDER BY o.id DESC LIMIT ?""",
                    (max(1, limit),),
                )
            )

    def performance_summary(self, start_date: str | None = None) -> dict[str, object]:
        with self.connect() as con:
            row = con.execute(
                """SELECT COUNT(*) AS trades,
                          SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS wins,
                          COALESCE(AVG(CASE WHEN realized_pnl>0 THEN realized_pnl END), 0) AS average_win,
                          COALESCE(AVG(CASE WHEN realized_pnl<=0 THEN realized_pnl END), 0) AS average_loss,
                          COALESCE(SUM(realized_pnl), 0) AS net_pnl,
                          COALESCE(SUM(CASE WHEN realized_pnl>0 THEN realized_pnl ELSE 0 END), 0) AS gross_profit,
                          ABS(COALESCE(SUM(CASE WHEN realized_pnl<0 THEN realized_pnl ELSE 0 END), 0)) AS gross_loss
                   FROM paper_orders
                   WHERE status='CLOSED' AND (? IS NULL OR trading_date>=?)""",
                (start_date, start_date),
            ).fetchone()
            pnl_rows = con.execute(
                """SELECT realized_pnl FROM paper_orders
                   WHERE status='CLOSED' AND (? IS NULL OR trading_date>=?)
                   ORDER BY closed_at, id""",
                (start_date, start_date),
            ).fetchall()
        result = {key: row[key] or 0 for key in row.keys()}
        trades = int(result["trades"])
        result["win_rate"] = float(result["wins"]) / trades if trades else 0.0
        loss = float(result["gross_loss"])
        result["profit_factor"] = float(result["gross_profit"]) / loss if loss else None
        average_loss = abs(float(result["average_loss"]))
        result["reward_risk"] = (
            float(result["average_win"]) / average_loss if average_loss else None
        )
        equity = peak = max_drawdown = 0.0
        for pnl_row in pnl_rows:
            equity += float(pnl_row[0])
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        result["max_drawdown"] = round(max_drawdown, 2)
        return result

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
