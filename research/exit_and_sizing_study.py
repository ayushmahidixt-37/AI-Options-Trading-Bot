"""Exit shape and position size -- the two levers left after filters were exhausted.

Feature ranking showed 118 of 120 quintile cells lose money and that entry
selection is already working for a real slice of trades: cheap options win 51.6%
and near-expiry ones 50.3%, both still losing at about -18 per trade against ~40
in fees. The loss happens between gross and net, so the remaining levers are the
exit shape and the position size, neither of which is a filter.

Three phases, ordered so the small sample is never used to make a choice it
cannot support:

**Phase 1 -- exit shape on the FULL baseline sample.** RSI 60/40 keeps only
22-83 trades per window; choosing among ten exit variants on that would be
fitting noise. The exit shape is therefore selected on all 2,388 baseline trades,
which is a much larger sample and, importantly, is chosen *independently* of the
RSI filter rather than tuned alongside it.

**Phase 2 -- apply the chosen shape to RSI 60/40.** The shape has to survive on
the filtered trades too, across all five windows. If it only helps the baseline,
it is not adopted.

**Phase 3 -- position size.** In the rolling account the fee is a flat 40 per
trade while units scale with lots, so a larger position amortises the same fixed
cost over more units: 40 is ~1% of a Rs 3,750 position but 0.27% of a Rs 15,000
one. This measures how much of the cost drag that actually recovers, and what it
does to drawdown -- because bigger positions cut cost drag and magnify losses at
the same time.

Ranked by consistency across five windows, not by total: a variant that wins big
in one window and loses in four is noise, whatever its sum says.

Usage:
    python research/exit_and_sizing_study.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import run_upstox_backtest  # noqa: E402
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

from capital_compounding_simulation import simulate  # noqa: E402

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
BASE = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)
RSI_ONLY = replace(BASE, bullish_rsi_min=60, bearish_rsi_max=40)

EXIT_SHAPES = [
    ("current: stop1.6 tgt0.30", {}),
    ("tgt 0.50", {"target_return": 0.50}),
    ("tgt 0.80", {"target_return": 0.80}),
    ("tgt none (uncapped)", {"target_return": None}),
    ("stop 2.5 tgt 0.30", {"stop_risk_fraction": 2.5}),
    ("stop 2.5 tgt 0.80", {"stop_risk_fraction": 2.5, "target_return": 0.80}),
    ("trail0.40 act0.30 no tgt", {"target_return": None, "trailing_stop": 0.40,
                                  "trailing_activation_return": 0.30}),
    ("trail0.30 act0.50 no tgt", {"target_return": None, "trailing_stop": 0.30,
                                  "trailing_activation_return": 0.50}),
    ("stop2.5 trail0.40 act0.30", {"stop_risk_fraction": 2.5, "target_return": None,
                                   "trailing_stop": 0.40, "trailing_activation_return": 0.30}),
]
WINDOWS = [
    ("2020-08..12", date(2020, 8, 3), date(2020, 12, 31)),
    ("2021", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023-2024", date(2023, 1, 1), date(2024, 10, 1)),
    ("2025-2026", date(2025, 3, 1), date(2026, 8, 18)),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--capital", type=float, default=100_000.0)
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    def run(params, start, end):
        return run_upstox_backtest(
            archive, strategy=CANDIDATE_B, start=start, end=end, settings=settings,
            parameters=params, underlying_key=NIFTY_UNDERLYING_KEY,
            timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
        )

    print("PHASE 1 -- exit shape on the FULL baseline sample (2,388 trades, filter-independent)")
    print("=" * 126)
    print(f"  {'exit shape':<28}" + "".join(f"{w[0]:>17}" for w in WINDOWS) + f"{'windows>0':>12}")
    print("-" * 126)
    phase1 = []
    for label, overrides in EXIT_SHAPES:
        params = replace(BASE, **overrides)
        cells, positive, total = [], 0, 0.0
        for _name, start, end in WINDOWS:
            result = run(params, start, end)
            cells.append(result.net_pnl)
            total += result.net_pnl
            if result.net_pnl > 0:
                positive += 1
        phase1.append((positive, total, label, overrides))
        print(f"  {label:<28}" + "".join(f"{c:>+17,.0f}" for c in cells) + f"{positive:>9}/5",
              flush=True)

    phase1.sort(key=lambda row: (-row[0], -row[1]))
    best_positive, best_total, best_label, best_overrides = phase1[0]
    print(f"\n  best exit shape on baseline: {best_label} "
          f"({best_positive}/5 windows positive, total {best_total:+,.0f})")

    print("\n\nPHASE 2 -- does that shape survive on RSI 60/40's filtered trades?")
    print("=" * 126)
    print(f"  {'variant':<34}" + "".join(f"{w[0]:>17}" for w in WINDOWS)
          + f"{'windows>0':>12}{'total':>14}")
    print("-" * 126)
    phase2 = {}
    for label, params in (("RSI 60/40 + current exit", RSI_ONLY),
                          (f"RSI 60/40 + {best_label}", replace(RSI_ONLY, **best_overrides))):
        cells, positive, total, trades = [], 0, 0.0, []
        for _name, start, end in WINDOWS:
            result = run(params, start, end)
            cells.append(result.net_pnl)
            total += result.net_pnl
            trades.extend(result.trade_details)
            if result.net_pnl > 0:
                positive += 1
        phase2[label] = (trades, total)
        print(f"  {label:<34}" + "".join(f"{c:>+17,.0f}" for c in cells)
              + f"{positive:>9}/5{total:>+14,.0f}", flush=True)

    print("\n\nPHASE 3 -- position size: does a larger position recover the fixed-cost drag?")
    print("Fee is a flat 40 per trade while units scale with lots, so a bigger position")
    print("amortises the same cost over more units -- and magnifies losses equally.")
    print("=" * 126)
    print(f"  {'variant':<34}{'risk/trade':>12}{'final':>12}{'return':>10}"
          f"{'max DD':>12}{'taken':>8}")
    print("-" * 126)
    for label, (trades, _total) in phase2.items():
        if not trades:
            continue
        for risk_pct in (0.01, 0.02, 0.04, 0.06, 0.10):
            outcome = simulate(sorted(trades, key=lambda t: t.entry_at), args.capital,
                               settings.paper_fee_per_order, None, 0.5,
                               sizing="risk", risk_pct=risk_pct)
            print(f"  {label:<34}{risk_pct:>11.0%}{outcome['final_balance']:>12,.0f}"
                  f"{outcome['total_return_pct']:>+9.1f}%{outcome['max_drawdown']:>12,.0f}"
                  f"{outcome['trades_taken']:>8}", flush=True)
        print()

    print("\nRANDOM CONTROL on the best combination (does it beat same-size random picks?)")
    best_key = max(phase2, key=lambda k: phase2[k][1])
    best_trades = phase2[best_key][0]
    for _name, start, end in WINDOWS:
        base = run(BASE, start, end)
        subset = [t for t in best_trades if start <= t.entry_at.date() <= end]
        if not subset or not base.trades:
            continue
        size = min(len(subset), base.trades)
        draws = sorted(sum(t.net_pnl for t in random.Random(s).sample(list(base.trade_details), size))
                       for s in range(120))
        actual = sum(t.net_pnl for t in subset)
        beat = sum(1 for d in draws if actual > d)
        print(f"  {_name:<14} n={len(subset):>4} pnl={actual:>+11,.0f} "
              f"random med={statistics.median(draws):>+11,.0f}  beat {beat}/120 "
              f"({beat / 1.2:>5.0f}th){'  <--' if beat >= 114 else ''}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
