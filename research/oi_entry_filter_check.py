"""Does open-interest movement BEFORE entry predict a trade's outcome?

Corrects a lookahead flaw in `research/trade_postmortem.py`'s open-interest
screen. That screen measured OI change from entry to the session's end and
found trades with rising OI won at 17% versus 38% — but the window it measured
extends past the entry decision and past the exit, so it uses information that
does not exist at the moment a filter would have to act. It is a description of
what accompanied failure, not a usable predictor.

This measures the same idea causally: open interest over the ``--lookback``
candles strictly BEFORE entry, on the contract actually traded, compared with
how the trade then turned out. Nothing after the entry timestamp is read.

If pre-entry OI direction separates winners from losers, it is a genuine entry
filter and the first signal-level lead this project has found. If it does not,
the original observation was an artefact of the lookahead window and should be
dropped — which is the more likely outcome and the reason this check exists.

Usage:
    python research/oi_entry_filter_check.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
from datetime import date

from options_bot.backtest import BacktestParameters
from options_bot.backtest_cli import _settings_for_archive
from options_bot.market_archive import MarketArchive
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy
from options_bot.upstox_backtest import run_upstox_backtest
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2021-12-31")
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    parser.add_argument("--lookback", type=int, default=6,
                        help="Candles strictly before entry over which to measure OI change.")
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B,
        start=date.fromisoformat(args.start), end=date.fromisoformat(args.end),
        settings=settings, parameters=PARAMS,
        underlying_key=args.underlying_key, timeframe=args.timeframe,
        include_dhan=True, include_derived=True,
    )
    print(f"Pre-entry OI check -- {result.trades} trades, {args.start}..{args.end}, "
          f"{args.timeframe}, lookback={args.lookback} candles")
    print("No data at or after the entry timestamp is read.")
    print("=" * 96)

    rows = []
    with archive.connect() as con:
        for trade in result.trade_details:
            prior = con.execute(
                """SELECT open_interest FROM market_candles
                   WHERE instrument_token=? AND timeframe=? AND started_at<?
                     AND open_interest IS NOT NULL
                   ORDER BY started_at DESC LIMIT ?""",
                (trade.token, args.timeframe, trade.entry_at.isoformat(), args.lookback),
            ).fetchall()
            if len(prior) < 2:
                continue
            newest = float(prior[0][0])
            oldest = float(prior[-1][0])
            if oldest <= 0:
                continue
            rows.append({
                "oi_delta": (newest - oldest) / oldest,
                "won": trade.net_pnl > 0,
                "pnl": trade.net_pnl,
            })

    if not rows:
        print("No trades had usable pre-entry open-interest history.")
        return 1

    base_win = sum(1 for r in rows if r["won"]) / len(rows) * 100
    base_pnl = sum(r["pnl"] for r in rows)
    print(f"\nUsable trades: {len(rows)}   baseline win rate {base_win:.1f}%   "
          f"total P&L {base_pnl:,.2f}\n")

    rising = [r for r in rows if r["oi_delta"] > 0]
    falling = [r for r in rows if r["oi_delta"] <= 0]
    print(f"{'Bucket':<22}{'n':>6}{'win rate':>11}{'total P&L':>15}{'P&L/trade':>12}")
    for name, group in (("pre-entry OI rising", rising), ("pre-entry OI falling/flat", falling)):
        if not group:
            print(f"{name:<22}{0:>6}")
            continue
        wins = sum(1 for r in group if r["won"])
        total = sum(r["pnl"] for r in group)
        print(f"{name:<22}{len(group):>6}{wins / len(group) * 100:>10.1f}%"
              f"{total:>15,.2f}{total / len(group):>12,.2f}")

    # Quantile view: does the effect grow monotonically with the size of the move?
    ordered = sorted(rows, key=lambda r: r["oi_delta"])
    print(f"\n{'Quartile of pre-entry OI change':<34}{'n':>6}{'win rate':>11}{'P&L/trade':>12}")
    size = len(ordered) // 4
    for index in range(4):
        chunk = ordered[index * size: (index + 1) * size] if index < 3 else ordered[3 * size:]
        if not chunk:
            continue
        wins = sum(1 for r in chunk if r["won"])
        total = sum(r["pnl"] for r in chunk)
        lo = chunk[0]["oi_delta"] * 100
        hi = chunk[-1]["oi_delta"] * 100
        label = f"Q{index + 1}  [{lo:+.1f}% .. {hi:+.1f}%]"
        print(f"{label:<34}{len(chunk):>6}{wins / len(chunk) * 100:>10.1f}%{total / len(chunk):>12,.2f}")

    if rising and falling:
        gap = (sum(1 for r in falling if r["won"]) / len(falling)
               - sum(1 for r in rising if r["won"]) / len(rising)) * 100
        print(f"\nWin-rate gap (falling minus rising): {gap:+.1f} percentage points")
        print("Lookahead version reported by trade_postmortem.py: +21.0 points (38% vs 17%)")
        print("\nIf this gap is far smaller than the lookahead version, the original")
        print("observation was an artefact of reading post-entry data and should be dropped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
