"""Materialize 5/10/15-minute candles from already-archived 1-minute data.

Windows-dev-machine-only (like train_signal_quality_model.py), but this one
only touches the local archive -- no network calls. Reads every
``timeframe='ONE_MINUTE', source='upstox'`` instrument token, resamples via
``candle_resample.resample_candles``, and writes the derived bars back as
ordinary ``market_candles`` rows under the target timeframe.

Safe to re-run: `market_candles`'s PRIMARY KEY is
(instrument_token, timeframe, started_at), and every write goes through the
existing INSERT OR IGNORE path (`MarketArchive.save_upstox_candles`) -- a
derived bar can never overwrite an already-archived real bar for the same
token/timeframe/timestamp (e.g. the real FIVE_MINUTE data already pulled
for Jan-Aug 2026 is left exactly as-is; only genuinely missing slots, like
the newly-pulled Oct 2024-Dec 2025 range, actually get filled in).

Every row this script writes is tagged ``derived_from_timeframe='ONE_MINUTE'``
so it can never again be silently mistaken for a real, directly-fetched Upstox
bar -- a real bar this project's backtest results were built on. An earlier
run of this exact script (2026-08-21, before this tag existed) did exactly
that: it materialized candles for every one-minute-covered instrument, not
just the near-ATM ones the real ingestion targeted, silently mixing them into
the same source='upstox' rows the documented BACKTEST_FINDINGS.md numbers
came from, and made every prior finding non-reproducible. See that file's
2026-08-21 data-integrity entry. Any query used to reproduce or extend a
documented finding must filter ``WHERE derived_from_timeframe IS NULL``.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from options_bot.candle_resample import resample_candles
from options_bot.candles import Candle
from options_bot.market_archive import MarketArchive
from options_bot.upstox_data import UpstoxCandle

TARGET_TIMEFRAMES = {"FIVE_MINUTE": 5, "TEN_MINUTE": 10, "FIFTEEN_MINUTE": 15}


def _distinct_one_minute_tokens(archive: MarketArchive) -> list[tuple[str, str]]:
    with archive.connect() as con:
        rows = con.execute(
            """SELECT DISTINCT instrument_token, exchange_name FROM market_candles
               WHERE source='upstox' AND timeframe='ONE_MINUTE'"""
        ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def _load_one_minute_candles(archive: MarketArchive, token: str) -> list[Candle]:
    with archive.connect() as con:
        rows = con.execute(
            """SELECT symbol, started_at, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source='upstox' AND timeframe='ONE_MINUTE'
               ORDER BY started_at""",
            (token,),
        ).fetchall()
    return [
        Candle(
            symbol=str(row[0]), started_at=datetime.fromisoformat(row[1]),
            open=float(row[2]), high=float(row[3]), low=float(row[4]), close=float(row[5]),
        )
        for row in rows
    ]


def _already_materialized_tokens(archive: MarketArchive) -> set[str]:
    """Tokens that already have FIVE_MINUTE rows -- used as a cheap 'done'
    proxy so repeated chunked invocations skip already-processed tokens
    instead of redoing all prior work every time."""
    with archive.connect() as con:
        rows = con.execute(
            "SELECT DISTINCT instrument_token FROM market_candles WHERE source='upstox' AND timeframe='FIVE_MINUTE'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--offset", type=int, default=0, help="Skip this many tokens before starting")
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many tokens")
    parser.add_argument(
        "--redo-existing", action="store_true",
        help="Don't skip tokens that already have FIVE_MINUTE rows (normally skipped for fast re-runs)",
    )
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    now = datetime.now()

    all_tokens = _distinct_one_minute_tokens(archive)
    print(f"tokens_with_one_minute_data={len(all_tokens)}")

    if not args.redo_existing:
        done = _already_materialized_tokens(archive)
        all_tokens = [(token, exchange) for token, exchange in all_tokens if token not in done]
        print(f"already_materialized_skipped={len(done)}, remaining={len(all_tokens)}")

    end = len(all_tokens) if args.limit is None else min(len(all_tokens), args.offset + args.limit)
    tokens = all_tokens[args.offset:end]
    print(f"this_run_will_process={len(tokens)} (offset={args.offset}, end={end})")

    totals = {timeframe: 0 for timeframe in TARGET_TIMEFRAMES}
    for index, (token, exchange) in enumerate(tokens, 1):
        one_minute = _load_one_minute_candles(archive, token)
        if not one_minute:
            continue
        for timeframe, bucket_minutes in TARGET_TIMEFRAMES.items():
            bars = resample_candles(one_minute, bucket_minutes=bucket_minutes)
            if not bars:
                continue
            saved = archive.save_upstox_candles(
                [
                    UpstoxCandle(bar.symbol, bar.started_at, bar.open, bar.high, bar.low, bar.close)
                    for bar in bars
                ],
                token=token,
                exchange=exchange,
                timeframe=timeframe,
                collected_at=now,
                derived_from_timeframe="ONE_MINUTE",
            )
            totals[timeframe] += saved
        if index % 50 == 0 or index == len(tokens):
            print(f"processed {index}/{len(tokens)} tokens this run", flush=True)

    for timeframe, count in totals.items():
        print(f"{timeframe}: {count} new candles saved")

    with archive.connect() as con:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print("wal checkpointed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
