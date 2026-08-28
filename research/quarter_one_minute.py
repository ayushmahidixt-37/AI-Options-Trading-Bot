"""Stage 2: re-run a chosen quarter at 1-minute resolution, Rs 1,00,000 rolling.

Moving from 5-minute to 1-minute bars is not a neutral change of resolution: the
strategy's periods are counted in BARS, so `macro_period=60` means 300 minutes on
5-minute candles but only 60 minutes on 1-minute ones. Running the same numbers
on finer bars therefore silently tests a five-times-faster strategy rather than
the same strategy seen more closely. Both readings are run here and reported
separately:

- **same periods** (5/10/60/21) -- reacts 5x faster in wall-clock terms
- **scaled periods** (25/50/300/105) -- same wall-clock lookback as the 5-minute
  configuration, which is the honest like-for-like comparison

Each is run with and without the RSI 60/40 conviction band, and scored under
Rs 1,00,000 of rolling capital with risk-based sizing and real costs.

Warm-up is handled by starting the engine well before the quarter and filtering
to it afterwards. A short run starves the macro EMA and silently produces
different trades for the same dates.

Usage:
    python research/quarter_one_minute.py --archive .termux-data/market-data.sqlite3 \\
        --quarter-start 2024-04-01 --quarter-end 2024-06-30
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import run_upstox_backtest  # noqa: E402
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

from capital_compounding_simulation import simulate  # noqa: E402

BASE = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)
RSI = replace(BASE, bullish_rsi_min=60, bearish_rsi_max=40)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--quarter-start", required=True)
    parser.add_argument("--quarter-end", required=True)
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--out", default=None, help="Write the chosen run's trades to CSV.")
    args = parser.parse_args(argv)

    qs = date.fromisoformat(args.quarter_start)
    qe = date.fromisoformat(args.quarter_end)
    run_start = qs - timedelta(days=args.warmup_days)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    print(f"Quarter {qs}..{qe}   engine runs from {run_start} for warm-up, then filtered")
    print(f"Rs {args.capital:,.0f} rolling, risk-sized 2%, real costs")
    print("=" * 122)

    configs = [
        ("5-min  same periods", "FIVE_MINUTE",
         TrendConfirmedMomentumStrategy(fast_period=5, slow_period=10, macro_period=60, rsi_period=21)),
        ("1-min  same periods (5x faster)", "ONE_MINUTE",
         TrendConfirmedMomentumStrategy(fast_period=5, slow_period=10, macro_period=60, rsi_period=21)),
        ("1-min  scaled periods (like-for-like)", "ONE_MINUTE",
         TrendConfirmedMomentumStrategy(fast_period=25, slow_period=50, macro_period=300, rsi_period=105)),
    ]

    kept: dict[str, list] = {}
    for label, timeframe, strategy in configs:
        for variant, params in (("baseline", BASE), ("RSI 60/40", RSI)):
            t0 = time.time()
            result = run_upstox_backtest(
                archive, strategy=strategy, start=run_start, end=qe, settings=settings,
                parameters=params, underlying_key=NIFTY_UNDERLYING_KEY,
                timeframe=timeframe, include_dhan=True, include_derived=True,
            )
            trades = [t for t in result.trade_details if qs <= t.entry_at.date() <= qe]
            tag = f"{label} | {variant}"
            if not trades:
                print(f"  {tag:<46} (no trades in quarter)  [{time.time() - t0:.0f}s]", flush=True)
                continue
            fixed = sum(t.net_pnl for t in trades)
            wins = sum(1 for t in trades if t.net_pnl > 0)
            outcome = simulate(trades, args.capital, settings.paper_fee_per_order,
                               None, 0.5, sizing="risk", risk_pct=0.02)
            kept[tag] = trades
            print(f"  {tag:<46} n={len(trades):>4} win={wins / len(trades) * 100:>4.1f}% "
                  f"fixed={fixed:>+10,.0f} | Rs1L={outcome['final_balance']:>9,.0f} "
                  f"({outcome['total_return_pct']:>+6.1f}%) taken={outcome['trades_taken']} "
                  f"[{time.time() - t0:.0f}s]", flush=True)

    if args.out and kept:
        best = max(kept, key=lambda k: sum(t.net_pnl for t in kept[k]))
        import csv
        with open(args.out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["config", "signal_at", "entry_at", "exit_at", "direction", "symbol",
                             "token", "entry_price", "stop_price", "exit_price", "exit_reason",
                             "units", "net_pnl", "raw_points"])
            for tag, trades in kept.items():
                for t in sorted(trades, key=lambda x: x.entry_at):
                    writer.writerow([tag, t.signal_at.isoformat(), t.entry_at.isoformat(),
                                     t.exit_at.isoformat(), t.direction, t.symbol, t.token,
                                     t.entry_price, t.stop_price, t.exit_price, t.exit_reason,
                                     t.units, t.net_pnl, t.raw_points])
        print(f"\n  wrote all configs' trades to {args.out} (best by fixed P&L: {best})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
