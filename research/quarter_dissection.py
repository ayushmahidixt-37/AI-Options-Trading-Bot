"""Stage 3: dissect every trade in a quarter -- why it was taken, and what would have been better.

Takes one configuration's trades for a quarter and, for each one, reconstructs
the state at the moment of entry and the option's full path afterwards, so the
question "what should we have done differently" is answered from the data rather
than from a story about it.

Per trade it records:

- **What the signal saw**: RSI, ATR, EMA gap and the strategy's own confidence,
  as of the signal bar. Nothing after it.
- **Open interest**, both the level at entry and the direction of travel over the
  preceding bars -- causal, never reading past the entry timestamp. An earlier
  OI screen in this project was invalidated by measuring a window that extended
  past the exit; that error is not repeated here.
- **MAE / MFE** while the position was open: the worst and best it reached.
- **The best exit that existed**: the highest price available between entry and
  the session's force-exit, and when it occurred. This is the ceiling any exit
  rule could have captured -- useful as a bound, not as a target, since no rule
  can systematically sell the high.
- **Post-exit path**: whether the position recovered after we left, which
  separates "the stop was right" from "we were shaken out".

It then aggregates: what actually distinguishes the winners from the losers, and
at what MAE threshold the two populations separate -- which is the empirical
basis for a stop rule, as opposed to a guessed one.

Measurement only. Nothing here changes a strategy.

Usage:
    python research/quarter_dissection.py --archive .termux-data/market-data.sqlite3 \\
        --quarter-start 2024-04-01 --quarter-end 2024-06-30 --timeframe ONE_MINUTE --scaled
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.candles import Candle  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import (  # noqa: E402
    generate_signals_from_candles,
    run_upstox_backtest,
)
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

BASE = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--quarter-start", required=True)
    parser.add_argument("--quarter-end", required=True)
    parser.add_argument("--timeframe", default="ONE_MINUTE")
    parser.add_argument("--scaled", action="store_true",
                        help="Use 25/50/300/105 so the wall-clock lookback matches the 5-minute config.")
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--oi-lookback", type=int, default=15, help="Bars before entry for OI trend.")
    parser.add_argument("--max-days", type=int, default=8, help="How many days to print in full.")
    args = parser.parse_args(argv)

    qs, qe = date.fromisoformat(args.quarter_start), date.fromisoformat(args.quarter_end)
    run_start = qs - timedelta(days=args.warmup_days)
    strategy = (TrendConfirmedMomentumStrategy(fast_period=25, slow_period=50,
                                               macro_period=300, rsi_period=105)
                if args.scaled else
                TrendConfirmedMomentumStrategy(fast_period=5, slow_period=10,
                                               macro_period=60, rsi_period=21))

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    result = run_upstox_backtest(
        archive, strategy=strategy, start=run_start, end=qe, settings=settings,
        parameters=BASE, underlying_key=NIFTY_UNDERLYING_KEY,
        timeframe=args.timeframe, include_dhan=True, include_derived=True,
    )
    trades = [t for t in result.trade_details if qs <= t.entry_at.date() <= qe]
    print(f"DISSECTION {qs}..{qe}  timeframe={args.timeframe}  "
          f"periods={'scaled 25/50/300/105' if args.scaled else 'default 5/10/60/21'}")
    print(f"{len(trades)} trades, net {sum(t.net_pnl for t in trades):+,.2f}")
    print("=" * 132)

    with archive.connect() as con:
        rows = con.execute(
            """SELECT started_at, symbol, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source IN ('upstox','dhan') AND timeframe=?
                 AND derived_from_timeframe IS NULL AND started_at>=? AND started_at<?
               ORDER BY started_at""",
            (NIFTY_UNDERLYING_KEY, args.timeframe, run_start.isoformat(),
             (qe + timedelta(days=1)).isoformat()),
        ).fetchall()
        candles = [Candle(symbol=str(r[1]), started_at=datetime.fromisoformat(r[0]),
                          open=float(r[2]), high=float(r[3]), low=float(r[4]), close=float(r[5]))
                   for r in rows]
        observations = {o.observed_at: o for o in generate_signals_from_candles(candles, strategy)}

        records = []
        for trade in trades:
            obs = observations.get(trade.signal_at)
            session_end = datetime.combine(trade.entry_at.date(), settings.force_exit,
                                           tzinfo=trade.entry_at.tzinfo).isoformat()
            path = con.execute(
                """SELECT started_at, high, low, close, open_interest FROM market_candles
                   WHERE instrument_token=? AND timeframe=? AND started_at>=? AND started_at<=?
                   ORDER BY started_at""",
                (trade.token, args.timeframe, trade.entry_at.isoformat(), session_end),
            ).fetchall()
            if not path:
                continue
            prior_oi = con.execute(
                """SELECT open_interest FROM market_candles
                   WHERE instrument_token=? AND timeframe=? AND started_at<?
                     AND open_interest IS NOT NULL
                   ORDER BY started_at DESC LIMIT ?""",
                (trade.token, args.timeframe, trade.entry_at.isoformat(), args.oi_lookback),
            ).fetchall()
            oi_now = float(prior_oi[0][0]) if prior_oi else None
            oi_trend = None
            if len(prior_oi) >= 2 and float(prior_oi[-1][0]):
                oi_trend = (float(prior_oi[0][0]) - float(prior_oi[-1][0])) / float(prior_oi[-1][0])

            exit_iso = trade.exit_at.isoformat()
            during = [p for p in path if p[0] <= exit_iso]
            after = [p for p in path if p[0] > exit_iso]
            entry = trade.entry_price
            mae = min(float(p[2]) for p in during) / entry - 1 if during else 0.0
            mfe = max(float(p[1]) for p in during) / entry - 1 if during else 0.0
            best_row = max(path, key=lambda p: float(p[1]))
            best_ever = float(best_row[1]) / entry - 1
            best_at = datetime.fromisoformat(best_row[0])
            post = (max(float(p[1]) for p in after) / entry - 1) if after else None
            records.append({
                "trade": trade, "obs": obs, "mae": mae, "mfe": mfe,
                "best_ever": best_ever, "best_at": best_at, "post": post,
                "oi": oi_now, "oi_trend": oi_trend,
                "won": trade.net_pnl > 0,
                "mins_to_best": (best_at - trade.entry_at).total_seconds() / 60,
            })

    by_day: dict[date, list] = defaultdict(list)
    for rec in records:
        by_day[rec["trade"].entry_at.date()].append(rec)
    worst_days = sorted(by_day, key=lambda d: sum(r["trade"].net_pnl for r in by_day[d]))

    print(f"\nWORST {args.max_days} DAYS, TRADE BY TRADE")
    print("-" * 132)
    for day in worst_days[:args.max_days]:
        recs = by_day[day]
        total = sum(r["trade"].net_pnl for r in recs)
        print(f"\n{day}   {len(recs)} trades   day P&L {total:+,.2f}")
        print(f"  {'time':<7}{'dir':<9}{'rsi':>6}{'atr':>7}{'conf':>6}{'OI':>12}{'OI trend':>10}"
              f"{'entry':>8}{'exit':>8}{'reason':<15}{'P&L':>10}{'MAE':>8}{'MFE':>8}"
              f"{'best avail':>11}{'@min':>6}")
        for rec in sorted(recs, key=lambda r: r["trade"].entry_at):
            t, o = rec["trade"], rec["obs"]
            print(f"  {t.entry_at.strftime('%H:%M'):<7}{t.direction:<9}"
                  f"{(o.rsi if o and o.rsi else 0):>6.1f}{(o.atr if o else 0):>7.2f}"
                  f"{(o.confidence if o else 0):>6.2f}"
                  f"{(rec['oi'] or 0):>12,.0f}"
                  f"{(pct(rec['oi_trend']) if rec['oi_trend'] is not None else '-'):>10}"
                  f"{t.entry_price:>8.2f}{t.exit_price:>8.2f}{t.exit_reason:<15}"
                  f"{t.net_pnl:>+10,.0f}{pct(rec['mae']):>8}{pct(rec['mfe']):>8}"
                  f"{pct(rec['best_ever']):>11}{rec['mins_to_best']:>6.0f}")

    winners = [r for r in records if r["won"]]
    losers = [r for r in records if not r["won"]]
    print(f"\n\nWHAT SEPARATES {len(winners)} WINNERS FROM {len(losers)} LOSERS")
    print("-" * 132)

    def compare(name, fn):
        w = [fn(r) for r in winners if fn(r) is not None]
        losing = [fn(r) for r in losers if fn(r) is not None]
        if not w or not losing:
            return
        print(f"  {name:<26} winners median={statistics.median(w):>12,.3f}   "
              f"losers median={statistics.median(losing):>12,.3f}")

    compare("RSI at signal", lambda r: r["obs"].rsi if r["obs"] and r["obs"].rsi else None)
    compare("ATR at signal", lambda r: r["obs"].atr if r["obs"] else None)
    compare("confidence", lambda r: r["obs"].confidence if r["obs"] else None)
    compare("open interest", lambda r: r["oi"])
    compare("OI trend before entry", lambda r: r["oi_trend"])
    compare("MAE", lambda r: r["mae"])
    compare("MFE", lambda r: r["mfe"])
    compare("best available (%)", lambda r: r["best_ever"])
    compare("minutes to best", lambda r: r["mins_to_best"])

    print("\n  MAE SEPARATION -- the empirical basis for any stop rule:")
    for threshold in (-0.03, -0.05, -0.08, -0.10, -0.15, -0.20):
        w = sum(1 for r in winners if r["mae"] <= threshold)
        losing = sum(1 for r in losers if r["mae"] <= threshold)
        print(f"    dipped past {threshold * 100:>4.0f}%:  winners {w:>3}/{len(winners)} "
              f"({w / max(1, len(winners)) * 100:>4.0f}%)   "
              f"losers {losing:>3}/{len(losers)} ({losing / max(1, len(losers)) * 100:>4.0f}%)")

    print("\n  HOW LONG UNTIL THE BEST PRICE APPEARED (winners only):")
    mins = sorted(r["mins_to_best"] for r in winners)
    if mins:
        print(f"    median {statistics.median(mins):.0f} min   "
              f"p25 {mins[len(mins) // 4]:.0f}   p75 {mins[3 * len(mins) // 4]:.0f}   "
              f"max {mins[-1]:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
