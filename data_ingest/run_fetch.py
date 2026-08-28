"""One command to fetch, retry every failure, resample and verify.

Wraps the whole pipeline so a fetch cannot be half-done without saying so. The
underlying functions already track which individual requests failed; nothing was
acting on that, so a run could report "success" while silently missing whole
strike/day combinations.

What it does, in order:

1. **Index candles first.** No underlying series means no EMA or RSI, so option
   rows are worthless however many arrive.
2. **Option ladder, cycle by cycle**, collecting every failed (strike, option
   type, cycle) triple rather than discarding it.
3. **Retry the failures at the end**, in rounds, until a round fixes nothing.
   Deferring retries matters: a rate-limited or briefly-unavailable request often
   succeeds minutes later, and retrying immediately just burns the same limit.
4. **Persist whatever still fails** to `failed_requests.json`, so a later run can
   pick up exactly those without refetching everything. Storage is idempotent —
   re-saving an existing candle is a no-op — so re-running is always safe.
5. **Resample to FIVE_MINUTE**, which the strategy needs and the engine does not
   derive at query time.
6. **Report** row counts, field coverage and anything still missing.

Resumable by design. If the Dhan token expires mid-run (~24h lifetime), fix it
and run the same command again: already-saved rows are skipped.

Usage:
    python data_ingest/run_fetch.py --start 2026-01-01 --end 2026-08-28
    python data_ingest/run_fetch.py --retry-only        # just re-attempt past failures
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from options_bot.credentials import load_credentials  # noqa: E402
from options_bot.dhan_data import DhanClient  # noqa: E402
from options_bot.dhan_ingest import (  # noqa: E402
    FailedRequest,
    pull_index_range,
    pull_range,
    resample_dhan_iv_to_five_minute,
    resample_dhan_options_to_five_minute,
    resample_dhan_underlying_to_five_minute,
    retry_failed_requests,
)
from options_bot.market_archive import MarketArchive  # noqa: E402

DEFAULT_DB = _REPO / "data_ingest" / "data" / "nifty_forward.sqlite3"
FAILED_LOG = _REPO / "data_ingest" / "data" / "failed_requests.json"


def _load_failed() -> list[FailedRequest]:
    if not FAILED_LOG.exists():
        return []
    return [
        FailedRequest(
            strike_label=item["strike_label"], option_type=item["option_type"],
            cycle_start=date.fromisoformat(item["cycle_start"]),
            cycle_end=date.fromisoformat(item["cycle_end"]),
            expiry=date.fromisoformat(item["expiry"]), error=item["error"],
        )
        for item in json.loads(FAILED_LOG.read_text(encoding="utf-8"))
    ]


def _save_failed(failed: list[FailedRequest]) -> None:
    FAILED_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for item in failed:
        row = asdict(item)
        for key in ("cycle_start", "cycle_end", "expiry"):
            row[key] = row[key].isoformat()
        payload.append(row)
    FAILED_LOG.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _report(archive: MarketArchive) -> None:
    with archive.connect() as con:
        print("\n  rows by timeframe/source:")
        for tf, src, n, lo, hi in con.execute(
            """SELECT timeframe, source, COUNT(*), MIN(date(started_at)), MAX(date(started_at))
               FROM market_candles GROUP BY timeframe, source ORDER BY timeframe, source"""
        ):
            print(f"    {tf:<13}{src:<9}{n:>12,}  {lo} .. {hi}")
        total, oi, iv, vol = con.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN open_interest IS NOT NULL THEN 1 ELSE 0 END),
                      SUM(CASE WHEN implied_volatility IS NOT NULL THEN 1 ELSE 0 END),
                      SUM(CASE WHEN volume IS NOT NULL THEN 1 ELSE 0 END)
               FROM market_candles"""
        ).fetchone()
        if total:
            print(f"\n  field coverage over {total:,} rows:")
            print(f"    open_interest      {(oi or 0) / total * 100:>6.1f}%")
            print(f"    implied_volatility {(iv or 0) / total * 100:>6.1f}%")
            print(f"    volume             {(vol or 0) / total * 100:>6.1f}%")
            if not vol:
                print("    NOTE: volume empty. Expected non-zero after 2026-08-28 -- "
                      "check the ingestion is the fixed version.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--start")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--retry-only", action="store_true",
                        help="Skip fetching; only re-attempt requests in failed_requests.json.")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--max-retry-rounds", type=int, default=3)
    parser.add_argument("--credentials", default=str(_REPO / "credentials.env"))
    args = parser.parse_args(argv)

    if not args.retry_only and not args.start:
        parser.error("--start is required unless --retry-only is given")

    archive = MarketArchive(args.db)
    archive.initialize()
    token = load_credentials(args.credentials)["DHAN_ACCESS_TOKEN"].strip()
    client = DhanClient(token)
    started = time.time()
    print(f"Target database: {args.db}")
    print(f"Started {datetime.now():%Y-%m-%d %H:%M}", flush=True)

    failed: list[FailedRequest] = _load_failed() if args.retry_only else []

    if not args.retry_only:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        if not args.skip_index:
            print(f"\n[1/4] index candles {start} .. {end}", flush=True)
            saved, warnings = pull_index_range(client, archive, start, end)
            print(f"  {saved:,} index candles saved", flush=True)
            for warning in warnings:
                print(f"  WARNING {warning}")
            if not saved:
                print("  No index candles. Everything downstream will be empty -- "
                      "check the token and the date range before continuing.")

        print(f"\n[2/4] option ladder {start} .. {end}", flush=True)

        def show(summary):
            note = f"  ({len(summary.failed_requests)} failed)" if summary.failed_requests else ""
            print(f"  expiry {summary.expiry}: {summary.candles_saved:,} candles, "
                  f"{summary.instruments_saved} instruments{note}", flush=True)

        summaries = pull_range(client, archive, start, end, on_cycle_done=show)
        for summary in summaries:
            failed.extend(summary.failed_requests)
        print(f"  {sum(s.candles_saved for s in summaries):,} option candles across "
              f"{len(summaries)} cycles")

    # Retry deferred to the end: a request that failed on a rate limit usually
    # succeeds later, and retrying immediately just consumes the same limit.
    if failed:
        print(f"\n[3/4] retrying {len(failed)} failed request(s)", flush=True)
        for round_number in range(1, args.max_retry_rounds + 1):
            saved, failed = retry_failed_requests(client, archive, failed)
            print(f"  round {round_number}: recovered {saved:,} candles, "
                  f"{len(failed)} still failing", flush=True)
            if not failed or saved == 0:
                break
    else:
        print("\n[3/4] nothing to retry")

    _save_failed(failed)
    if failed:
        print(f"  {len(failed)} request(s) still failing, written to {FAILED_LOG.name}")
        print("  Re-run with --retry-only later; storage is idempotent so nothing is duplicated.")
        for item in failed[:5]:
            print(f"    {item.expiry} {item.strike_label}/{item.option_type}: {item.error[:70]}")
    else:
        print("  all requests succeeded")

    print("\n[4/4] resampling to FIVE_MINUTE", flush=True)
    print(f"  underlying: {resample_dhan_underlying_to_five_minute(archive):,} rows", flush=True)
    print(f"  options:    {resample_dhan_options_to_five_minute(archive):,} rows", flush=True)
    print(f"  IV:         {resample_dhan_iv_to_five_minute(archive):,} rows", flush=True)

    _report(archive)
    print(f"\nDone in {time.time() - started:.0f}s.")
    print("Verify before trusting it:")
    print(f"  python clean_room/inspect_dataset.py --dataset {args.db}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
