"""Head-to-head: RSI band, OI threshold, and the two combined, across five windows.

Two filters emerged from dissecting failures: an RSI conviction band (from the
2021-03-02 whipsaw post-mortem) and a raised open-interest floor (from the
2024-Q2 winner/loser comparison). The RSI band held up across five windows; the
OI floor did not, and is included here mainly to see whether it adds anything on
top of RSI rather than on its own.

Stacking filters that were each selected against this same archive multiplies
overfitting risk instead of adding evidence, so the combined variant is treated
as the most suspect of the four, not the most promising. The bar it has to clear
is not "beats baseline" -- a filter that cuts trades beats a negative-expectancy
baseline for cost reasons alone -- but "beats a random selection of the same
number of trades", which is what the 120-draw control per cell measures.

Also prints a quarter-by-quarter table for every variant, because an aggregate
can hide a long losing run: the RSI band's five-window aggregate looks strong
while its last three quarters of 2024 were all negative.

Usage:
    python research/combined_filter_study.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import defaultdict
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
VARIANTS = [
    ("baseline", BASE),
    ("RSI 60/40", replace(BASE, bullish_rsi_min=60, bearish_rsi_max=40)),
    ("OI >= 3M", replace(BASE, minimum_open_interest=3_000_000)),
    ("RSI 60/40 + OI >= 3M", replace(BASE, bullish_rsi_min=60, bearish_rsi_max=40,
                                     minimum_open_interest=3_000_000)),
]
WINDOWS = [
    ("2020-08..12", date(2020, 8, 3), date(2020, 12, 31)),
    ("2021", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023-2024", date(2023, 1, 1), date(2024, 10, 1)),
    ("2025-2026", date(2025, 3, 1), date(2026, 8, 18)),
]


def quarter_of(day: date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--random-draws", type=int, default=120)
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

    print(f"Head-to-head, Rs {args.capital:,.0f} rolling, risk-sized 2%, real costs")
    print(f"Percentile = vs {args.random_draws} random same-size draws from the baseline's trades")
    print("=" * 124)

    percentiles: dict[str, list[float]] = defaultdict(list)
    positives: dict[str, int] = defaultdict(int)
    all_trades: dict[str, list] = defaultdict(list)

    for name, start, end in WINDOWS:
        print(f"\n{name}", flush=True)
        base = run(BASE, start, end)
        for label, params in VARIANTS:
            result = base if label == "baseline" else run(params, start, end)
            if not result.trades:
                print(f"  {label:<24} (no trades)", flush=True)
                continue
            all_trades[label].extend(result.trade_details)
            if result.net_pnl > 0:
                positives[label] += 1
            outcome = simulate(result.trade_details, args.capital, settings.paper_fee_per_order,
                               None, 0.5, sizing="risk", risk_pct=0.02)
            extra = ""
            if label != "baseline" and base.trades:
                size = min(result.trades, base.trades)
                draws = sorted(
                    sum(t.net_pnl for t in random.Random(seed).sample(list(base.trade_details), size))
                    for seed in range(args.random_draws)
                )
                beat = sum(1 for d in draws if result.net_pnl > d)
                pct = beat / len(draws) * 100
                percentiles[label].append(pct)
                extra = (f"  | random med={statistics.median(draws):>+9,.0f} "
                         f"{pct:>5.0f}th{'  <--' if pct >= 95 else ''}")
            print(f"  {label:<24} n={result.trades:>5} win={result.win_rate * 100:>4.1f}% "
                  f"pnl={result.net_pnl:>+10,.0f} PF={result.profit_factor or 0:>5.2f} "
                  f"| Rs1L={outcome['final_balance']:>9,.0f}{extra}", flush=True)

    print("\n\nSUMMARY ACROSS THE FIVE WINDOWS")
    print("-" * 124)
    print(f"  {'variant':<24}{'windows P&L>0':>16}{'mean percentile':>18}{'min':>8}{'total P&L':>14}")
    for label, _p in VARIANTS:
        if label == "baseline":
            total = sum(t.net_pnl for t in all_trades[label])
            print(f"  {label:<24}{positives[label]:>13}/5{'-':>18}{'-':>8}{total:>+14,.0f}")
            continue
        vals = percentiles[label]
        total = sum(t.net_pnl for t in all_trades[label])
        print(f"  {label:<24}{positives[label]:>13}/5"
              f"{statistics.mean(vals):>18.1f}{min(vals):>8.0f}{total:>+14,.0f}")

    print("\n\nQUARTER BY QUARTER (a good aggregate can hide a long losing run)")
    print("-" * 124)
    quarters = sorted({quarter_of(t.entry_at.date()) for t in all_trades["baseline"]})
    header = "  " + f"{'quarter':<10}" + "".join(f"{lab[:20]:>21}" for lab, _ in VARIANTS)
    print(header)
    for q in quarters:
        cells = ""
        for label, _p in VARIANTS:
            trades = [t for t in all_trades[label] if quarter_of(t.entry_at.date()) == q]
            cells += f"{(sum(t.net_pnl for t in trades) if trades else 0):>+15,.0f}{len(trades):>6}" \
                if trades else f"{'-':>15}{0:>6}"
        print(f"  {q:<10}{cells}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
