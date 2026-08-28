"""Inspect an exported dataset before evaluating against it.

Checks the shape of the data rather than trusting a filename: what date range it
actually covers, how many instruments, whether open interest and implied
volatility are populated, and whether the underlying index series has gaps large
enough to distort a backtest.

Worth running first every time. An evaluation on the wrong window, or on a series
with a fortnight missing from the middle, produces numbers that look like
evidence and are not.

Usage:
    python clean_room/inspect_dataset.py --dataset clean_room/data/dataset.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

NIFTY = "NSE_INDEX|Nifty 50"
# Must match STRATEGY.md. A dataset lacking this timeframe yields zero trades
# while every other health check still passes -- which is how a broken dataset
# reported itself as fine and produced an empty evaluation.
REQUIRED_TIMEFRAME = "FIVE_MINUTE"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--max-gap-days", type=int, default=4)
    args = parser.parse_args(argv)

    path = Path(args.dataset)
    if not path.exists():
        print(f"No such dataset: {path}")
        return 2
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    print(f"DATASET  {path.name}   ({path.stat().st_size / 1_048_576:,.0f} MB)")
    print("=" * 84)
    for timeframe, source, lo, hi, rows in con.execute(
        """SELECT timeframe, source, MIN(date(started_at)), MAX(date(started_at)), COUNT(*)
           FROM market_candles GROUP BY timeframe, source ORDER BY timeframe, source"""
    ):
        print(f"  {timeframe:<13} {source:<9} {lo} .. {hi}   {rows:>12,} rows")

    have = {r[0] for r in con.execute("SELECT DISTINCT timeframe FROM market_candles")}
    if REQUIRED_TIMEFRAME not in have:
        print(f"\n  FATAL: STRATEGY.md requires {REQUIRED_TIMEFRAME} candles and this dataset")
        print(f"  has only {sorted(have)}. The engine does not derive one timeframe from")
        print("  another -- it will find no candles and report zero trades. Re-export with")
        print(f"  --timeframes {REQUIRED_TIMEFRAME},ONE_MINUTE before evaluating.")
        return 1
    n_required = con.execute("SELECT COUNT(*) FROM market_candles WHERE timeframe=?",
                             (REQUIRED_TIMEFRAME,)).fetchone()[0]
    print(f"\n  strategy timeframe {REQUIRED_TIMEFRAME}: {n_required:,} rows present")

    total, tokens, oi, iv = con.execute(
        """SELECT COUNT(*), COUNT(DISTINCT instrument_token),
                  SUM(CASE WHEN open_interest IS NOT NULL THEN 1 ELSE 0 END),
                  SUM(CASE WHEN implied_volatility IS NOT NULL THEN 1 ELSE 0 END)
           FROM market_candles"""
    ).fetchone()
    print(f"\n  {total:,} candle rows across {tokens:,} instruments")
    print(f"  open interest present on {oi or 0:,} rows ({(oi or 0) / max(1, total) * 100:.1f}%)")
    print(f"  implied volatility on    {iv or 0:,} rows ({(iv or 0) / max(1, total) * 100:.1f}%)")

    instruments = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    expiries = con.execute(
        "SELECT MIN(expiry), MAX(expiry), COUNT(DISTINCT expiry) FROM instruments"
    ).fetchone()
    print(f"  {instruments:,} instrument definitions, {expiries[2]} distinct expiries "
          f"({expiries[0]} .. {expiries[1]})")

    underlying = [date.fromisoformat(r[0]) for r in con.execute(
        "SELECT DISTINCT date(started_at) FROM market_candles "
        "WHERE instrument_token=? AND timeframe=? ORDER BY 1",
        (NIFTY, REQUIRED_TIMEFRAME))]
    if not underlying:
        print("\n  WARNING: no candles for the NIFTY index itself. The strategy cannot run.")
        return 1

    print(f"\n  underlying index: {len(underlying)} trading days, "
          f"{underlying[0]} .. {underlying[-1]}")
    gaps = [(a, b, (b - a).days) for a, b in zip(underlying, underlying[1:])
            if (b - a).days > args.max_gap_days]
    if gaps:
        print(f"  WARNING: {len(gaps)} gap(s) longer than {args.max_gap_days} days:")
        for a, b, days in gaps[:10]:
            print(f"    {a} -> {b}  ({days} days)")
        print("  A backtest spanning a gap is not wrong, but its results are not continuous.")
    else:
        print(f"  no gaps longer than {args.max_gap_days} days")

    thin = con.execute(
        """SELECT date(started_at), COUNT(*) FROM market_candles
           WHERE instrument_token=? AND timeframe=?
           GROUP BY 1 HAVING COUNT(*) < 40 ORDER BY 1 LIMIT 10""",
        (NIFTY, REQUIRED_TIMEFRAME),
    ).fetchall()
    if thin:
        print(f"\n  {len(thin)} day(s) with unusually few index candles (partial sessions?):")
        for day, count in thin:
            print(f"    {day}: {count} candles")

    span = (underlying[-1] - underlying[0]).days
    print(f"\n  span {span} calendar days (~{span / 30.44:.1f} months)")
    print(f"  at roughly one trade per week this window would be expected to produce "
          f"~{span / 7:.0f} trades")
    if span < 300:
        print("  NOTE: PROTOCOL.md requires 40+ trades before a verdict carries weight.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
