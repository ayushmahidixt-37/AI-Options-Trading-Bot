"""Systematic global parameter search, scored the way a real account experiences it.

Six individual hypotheses have been tested and rejected, but a broad, systematic
sweep of the parameter space had never been run under the corrected methodology
(real costs, Rs 1,00,000 rolling capital, dev/val/held-out fixed in advance, a
random-selection control on the finalist). This is that sweep, built as a
reusable tool rather than a one-off: the grids are CLI-configurable so the same
machinery can re-run on a different range or a different strategy family.

Staged coordinate search rather than one full grid, because the full cross
product of every lever is combinatorially hopeless and mostly wasted on
obviously-bad corners:

  Stage 1  indicator periods   (fast / slow / macro / rsi)
  Stage 2  exit shell          (stop_risk_fraction x target_return), best Stage-1 fixed
  Stage 3  entry filters       (premium / OI / entry window / max hold), one lever at a time

Only Stage 1's winner feeds Stage 2, and so on. Everything above happens on the
DEVELOPMENT window alone; the validation window sees only the finalists, and the
held-out window sees exactly one configuration, once.

Ranking uses raw net P&L, not the Rs 1,00,000 final balance. Both are reported,
but the rolling-capital figure is badly path-dependent -- earlier work here saw
random same-size trade draws range from -37% to +117% on identical inputs -- so
ranking hundreds of configurations by it would mostly select for lucky ordering.
Net P&L is the stabler ranking signal; the account figure is what says whether a
winner is worth anything in practice.

The finalist faces a random control: N random subsets matched to its trade count.
A configuration that merely trades less will look good on a negative-expectancy
strategy for that reason alone, and this is the check that catches it.

Usage:
    python research/parameter_sweep.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import itertools
import random
import statistics
import sys
import time
from dataclasses import replace
from datetime import date, time as clock_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import run_upstox_backtest  # noqa: E402
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

from capital_compounding_simulation import simulate  # noqa: E402

BASE_PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def evaluate(archive, settings, strategy, params, start, end, capital):
    result = run_upstox_backtest(
        archive, strategy=strategy, start=start, end=end, settings=settings,
        parameters=params, underlying_key=NIFTY_UNDERLYING_KEY,
        timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
    )
    if result.trades == 0:
        return result, None
    outcome = simulate(result.trade_details, capital, settings.paper_fee_per_order,
                       None, 0.5, sizing="risk", risk_pct=0.02)
    return result, outcome


def line(label, result, outcome):
    if outcome is None:
        return f"  {label:<44} (no trades)"
    return (f"  {label:<44} n={result.trades:>4} win={result.win_rate * 100:>4.1f}% "
            f"pnl={result.net_pnl:>10,.0f} PF={result.profit_factor or 0:>5.2f} "
            f"| Rs1L={outcome['final_balance']:>9,.0f} ({outcome['total_return_pct']:>+6.1f}%)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--dev", default="2021-01-01:2021-12-31")
    parser.add_argument("--val", default="2022-01-01:2022-12-31")
    parser.add_argument("--heldout", default="2023-01-01:2024-10-01")
    parser.add_argument("--final", default="2025-03-01:2026-08-18")
    parser.add_argument("--finalists", type=int, default=5)
    parser.add_argument("--random-seeds", type=int, default=40)
    args = parser.parse_args(argv)

    def window(spec):
        a, b = spec.split(":")
        return date.fromisoformat(a), date.fromisoformat(b)

    dev_start, dev_end = window(args.dev)
    val_start, val_end = window(args.val)
    ho_start, ho_end = window(args.heldout)
    fi_start, fi_end = window(args.final)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    print(f"Global parameter sweep -- ranked by net P&L, Rs {args.capital:,.0f} account shown alongside")
    print(f"DEV {dev_start}..{dev_end}   VAL {val_start}..{val_end}   "
          f"HELD-OUT {ho_start}..{ho_end}   FINAL {fi_start}..{fi_end}")
    print("=" * 122)

    started = time.time()
    scored: list[tuple[float, str, object, BacktestParameters]] = []

    # ---------------- Stage 1: indicator periods ----------------
    print("\nSTAGE 1 -- indicator periods (development only)")
    grid = [
        (f, s, m, r)
        for f, s, m, r in itertools.product((3, 5, 8), (10, 13, 21), (30, 60, 90), (14, 21))
        if f < s
    ]
    for index, (fast, slow, macro, rsi) in enumerate(grid, 1):
        strategy = TrendConfirmedMomentumStrategy(
            fast_period=fast, slow_period=slow, macro_period=macro, rsi_period=rsi)
        result, outcome = evaluate(archive, settings, strategy, BASE_PARAMS,
                                   dev_start, dev_end, args.capital)
        label = f"fast={fast} slow={slow} macro={macro} rsi={rsi}"
        if outcome:
            scored.append((result.net_pnl, label, strategy, BASE_PARAMS))
        if index % 6 == 0 or index == len(grid):
            print(f"  [{index}/{len(grid)} done, {time.time() - started:.0f}s]", flush=True)
    scored.sort(key=lambda row: -row[0])
    print("  best 5 of stage 1:")
    for pnl, label, _s, _p in scored[:5]:
        print(f"    {label:<44} pnl={pnl:>10,.0f}")
    best_strategy = scored[0][2] if scored else TrendConfirmedMomentumStrategy()

    # ---------------- Stage 2: exit shell ----------------
    print("\nSTAGE 2 -- exit shell, best Stage-1 indicators fixed")
    stage2: list[tuple[float, str, BacktestParameters]] = []
    for srf, tgt in itertools.product((0.8, 1.2, 1.6, 2.0, None), (0.20, 0.30, 0.50, None)):
        params = replace(BASE_PARAMS, stop_risk_fraction=srf, target_return=tgt)
        result, outcome = evaluate(archive, settings, best_strategy, params,
                                   dev_start, dev_end, args.capital)
        label = f"stop={srf} target={tgt}"
        if outcome:
            stage2.append((result.net_pnl, label, params))
            print(line(label, result, outcome), flush=True)
    stage2.sort(key=lambda row: -row[0])
    best_params = stage2[0][2] if stage2 else BASE_PARAMS

    # ---------------- Stage 3: entry filters, one lever at a time ----------------
    print("\nSTAGE 3 -- entry filters, one lever at a time")
    stage3: list[tuple[float, str, BacktestParameters]] = []
    levers = []
    for value in (None, 20, 40):
        levers.append((f"min_premium={value}", replace(best_params, minimum_option_premium=value)))
    for value in (None, 100_000, 250_000):
        levers.append((f"min_oi={value}", replace(best_params, minimum_open_interest=value)))
    for label, win in (("entry=any", (None, None)),
                       ("entry=09:30-14:30", (clock_time(9, 30), clock_time(14, 30))),
                       ("entry=10:00-15:00", (clock_time(10, 0), clock_time(15, 0)))):
        levers.append((label, replace(best_params, entry_start=win[0], entry_end=win[1])))
    for value in (None, 60, 120):
        levers.append((f"max_hold={value}", replace(best_params, maximum_hold_minutes=value)))
    for label, params in levers:
        result, outcome = evaluate(archive, settings, best_strategy, params,
                                   dev_start, dev_end, args.capital)
        if outcome:
            stage3.append((result.net_pnl, label, params))
            print(line(label, result, outcome), flush=True)
    stage3.sort(key=lambda row: -row[0])

    finalists = [(lbl, prm) for _p, lbl, prm in stage3[:args.finalists]]
    if best_params not in [p for _l, p in finalists]:
        finalists.append(("stage-2 best (unfiltered)", best_params))

    # ---------------- Validation ----------------
    print(f"\nVALIDATION -- {len(finalists)} finalists on {val_start}..{val_end}")
    validated = []
    for label, params in finalists:
        result, outcome = evaluate(archive, settings, best_strategy, params,
                                   val_start, val_end, args.capital)
        if outcome:
            validated.append((result.net_pnl, label, params))
            print(line(label, result, outcome), flush=True)
    validated.sort(key=lambda row: -row[0])
    if not validated:
        print("\nNo finalist produced trades on validation. Nothing to test.")
        return 0
    champion_label, champion = validated[0][1], validated[0][2]
    print(f"\n  champion after validation: {champion_label}")

    # ---------------- Held-out, one shot ----------------
    print(f"\nHELD-OUT (single shot) -- {ho_start}..{ho_end}")
    ho_result, ho_outcome = evaluate(archive, settings, best_strategy, champion,
                                     ho_start, ho_end, args.capital)
    base_result, base_outcome = evaluate(archive, settings,
                                         TrendConfirmedMomentumStrategy(
                                             fast_period=5, slow_period=10,
                                             macro_period=60, rsi_period=21),
                                         BASE_PARAMS, ho_start, ho_end, args.capital)
    print(line(f"champion: {champion_label}", ho_result, ho_outcome))
    print(line("current live config (reference)", base_result, base_outcome))

    # ---------------- Final unseen range + random control ----------------
    print(f"\nFINAL UNSEEN RANGE -- {fi_start}..{fi_end}")
    fi_result, fi_outcome = evaluate(archive, settings, best_strategy, champion,
                                     fi_start, fi_end, args.capital)
    fb_result, fb_outcome = evaluate(archive, settings,
                                     TrendConfirmedMomentumStrategy(
                                         fast_period=5, slow_period=10,
                                         macro_period=60, rsi_period=21),
                                     BASE_PARAMS, fi_start, fi_end, args.capital)
    print(line(f"champion: {champion_label}", fi_result, fi_outcome))
    print(line("current live config (reference)", fb_result, fb_outcome))

    if fi_outcome and fb_result and fb_result.trades:
        randoms = []
        for seed in range(args.random_seeds):
            rng = random.Random(seed)
            count = min(fi_result.trades, fb_result.trades)
            picked = sorted(rng.sample(list(fb_result.trade_details), count),
                            key=lambda t: t.entry_at)
            randoms.append(simulate(picked, args.capital, settings.paper_fee_per_order,
                                    None, 0.5, sizing="risk",
                                    risk_pct=0.02)["total_return_pct"])
        randoms.sort()
        beaten = sum(1 for r in randoms if fi_outcome["total_return_pct"] > r)
        print(f"\n  random same-size control: median={statistics.median(randoms):+.1f}%  "
              f"range [{randoms[0]:+.1f}%..{randoms[-1]:+.1f}%]  "
              f"champion beat {beaten}/{len(randoms)}")
        print("\n" + "=" * 122)
        ok = (fi_outcome["total_return_pct"] > 0
              and beaten >= len(randoms) * 0.9
              and ho_outcome and ho_outcome["total_return_pct"] > (base_outcome or {}).get("total_return_pct", 0))
        print("VERDICT: champion survives held-out AND the random control." if ok else
              "VERDICT: no configuration survived. Parameter tuning does not rescue this strategy.")
    print(f"\nTotal sweep time: {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
