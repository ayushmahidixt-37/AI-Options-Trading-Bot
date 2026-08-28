"""Export a standalone 1-minute dataset for independent evaluation.

Produces a self-contained SQLite file holding everything needed to run the
strategy and nothing else: the NIFTY underlying's 1-minute candles, the option
contracts' 1-minute candles (with open interest and implied volatility), and the
instrument metadata. No research ledger, no notes, no results -- an evaluator
opening this file cannot see what was tried before or what any number is
"supposed" to be.

Indexes are rebuilt on the copy, because a query written against an unindexed
10 GB archive can take hours and look like a hang.

Usage:
    python clean_room/export_dataset.py --archive .termux-data/market-data.sqlite3 \\
        --start 2024-04-01 --end 2024-06-30 --out clean_room/data/dataset.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeframes", default="FIVE_MINUTE,ONE_MINUTE",
                        help="Comma-separated. MUST include the timeframe STRATEGY.md "
                             "specifies, or the dataset cannot produce a single trade.")
    parser.add_argument("--warmup-days", type=int, default=20,
                        help="Extra history before --start so the 60-bar macro EMA is warm.")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    fetch_from = start - timedelta(days=args.warmup_days)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    lo = fetch_from.isoformat()
    hi = (end + timedelta(days=1)).isoformat()

    timeframes = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]
    print(f"Exporting {timeframes} {fetch_from} .. {end}")
    print(f"  (evaluation window starts {start}; {args.warmup_days} days of warm-up included)")
    started = time.time()

    con = sqlite3.connect(str(out))
    con.executescript(
        """
        CREATE TABLE market_candles (
            instrument_token TEXT NOT NULL, symbol TEXT NOT NULL,
            exchange_name TEXT NOT NULL, timeframe TEXT NOT NULL,
            started_at TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
            low REAL NOT NULL, close REAL NOT NULL, source TEXT NOT NULL,
            collected_at TEXT NOT NULL, open_interest REAL,
            derived_from_timeframe TEXT, implied_volatility REAL,
            PRIMARY KEY(instrument_token, timeframe, started_at)
        );
        CREATE TABLE instruments (
            token TEXT PRIMARY KEY, symbol TEXT NOT NULL, exchange_name TEXT NOT NULL,
            underlying TEXT NOT NULL, option_type TEXT NOT NULL, strike REAL NOT NULL,
            expiry TEXT NOT NULL, lot_size INTEGER NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        """
    )
    con.execute("ATTACH DATABASE ? AS src", (str(Path(args.archive).resolve()),))
    placeholders = ",".join("?" for _ in timeframes)
    con.execute(
        f"""INSERT INTO market_candles
           SELECT instrument_token, symbol, exchange_name, timeframe, started_at,
                  open, high, low, close, source, collected_at, open_interest,
                  derived_from_timeframe, implied_volatility
           FROM src.market_candles
           WHERE timeframe IN ({placeholders}) AND started_at>=? AND started_at<?""",
        (*timeframes, lo, hi),
    )
    candles = con.total_changes
    con.execute(
        """INSERT INTO instruments
           SELECT token, symbol, exchange_name, underlying, option_type, strike,
                  expiry, lot_size, first_seen_at, last_seen_at
           FROM src.instruments WHERE underlying='NIFTY'"""
    )
    con.commit()
    con.execute("DETACH DATABASE src")

    print(f"  copied {candles:,} candle rows [{time.time() - started:.0f}s]", flush=True)
    print("  building indexes ...", flush=True)
    con.executescript(
        """
        CREATE INDEX candle_time_idx ON market_candles(started_at);
        CREATE INDEX market_candles_source_idx ON market_candles(source, instrument_token);
        CREATE INDEX instrument_expiry_idx ON instruments(underlying, expiry, strike);
        """
    )
    con.commit()

    tokens = con.execute("SELECT COUNT(DISTINCT instrument_token) FROM market_candles").fetchone()[0]
    span = con.execute("SELECT MIN(date(started_at)), MAX(date(started_at)) FROM market_candles").fetchone()
    oi = con.execute("SELECT COUNT(*) FROM market_candles WHERE open_interest IS NOT NULL").fetchone()[0]
    con.close()

    size_mb = out.stat().st_size / 1_048_576
    print(f"\n  wrote {out}  ({size_mb:,.0f} MB)")
    print(f"  {candles:,} rows, {tokens:,} instruments, {span[0]} .. {span[1]}, "
          f"{oi:,} rows carry open interest")
    print(f"  total {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
