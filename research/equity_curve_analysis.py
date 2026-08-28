"""What the headline return hides: the equity path at each risk level.

Phase 3 of the exit/sizing study reported +2,030% at 10% risk per trade against
+280% at 2%. Read as end-points those look like the same edge with a bigger
number attached. They are not, and the difference is invisible in a final
balance: drawdown in rupees is meaningless without the peak it fell from.

This reconstructs the full equity curve for each risk level and reports what an
account actually has to survive -- worst drawdown **as a percentage of the peak
it fell from**, the lowest balance ever reached, how long recovery took, and the
worst run of consecutive losing trades. A 19 lakh drawdown on an account that
peaks near 21 lakh is a ~90% loss of everything accumulated; nobody continues
trading a system through that, so a backtest that assumes they did is not
describing an achievable outcome.

Also breaks a single year down week by week and month by month, so the pattern
of good and bad stretches is visible rather than averaged away.

Usage:
    python research/equity_curve_analysis.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
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
RSI = replace(
    BacktestParameters(stop_risk_fraction=1.6, target_return=0.30,
                       minimum_option_premium=20, minimum_open_interest=100_000),
    bullish_rsi_min=60, bearish_rsi_max=40,
)
WINDOWS = [
    (date(2020, 8, 3), date(2020, 12, 31)), (date(2021, 1, 1), date(2021, 12, 31)),
    (date(2022, 1, 1), date(2022, 12, 31)), (date(2023, 1, 1), date(2024, 10, 1)),
    (date(2025, 3, 1), date(2026, 8, 18)),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--detail-year", default="2022")
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    trades = []
    for start, end in WINDOWS:
        result = run_upstox_backtest(
            archive, strategy=CANDIDATE_B, start=start, end=end, settings=settings,
            parameters=RSI, underlying_key=NIFTY_UNDERLYING_KEY,
            timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
        )
        trades.extend(result.trade_details)
    trades.sort(key=lambda t: t.entry_at)
    print(f"RSI 60/40: {len(trades)} trades, {trades[0].entry_at.date()} .. "
          f"{trades[-1].entry_at.date()}", flush=True)

    print(f"\nWHAT EACH RISK LEVEL ACTUALLY PUTS THE ACCOUNT THROUGH (from Rs {args.capital:,.0f})")
    print("=" * 126)
    print(f"  {'risk':>6}{'final':>13}{'return':>11}{'peak':>13}{'worst trough':>15}"
          f"{'max DD %':>11}{'worst streak':>14}{'trades to recover':>19}")
    print("-" * 126)

    curves = {}
    for risk in (0.01, 0.02, 0.04, 0.06, 0.10):
        outcome = simulate(trades, args.capital, settings.paper_fee_per_order,
                           None, 0.5, sizing="risk", risk_pct=risk)
        taken = [e for e in outcome["events"] if not e["skipped"]]
        curve = [(datetime.fromisoformat(e["exit_at"]), e["balance_after"], e["net_pnl"])
                 for e in taken]
        curves[risk] = curve
        peak = args.capital
        worst_dd_pct = 0.0
        trough_at_worst = args.capital
        peak_at_worst = args.capital
        peak_index = 0
        recover = 0
        streak = worst_streak = 0
        for i, (_when, balance, pnl) in enumerate(curve):
            if balance > peak:
                peak, peak_index = balance, i
            dd_pct = (peak - balance) / peak * 100 if peak else 0.0
            if dd_pct > worst_dd_pct:
                worst_dd_pct = dd_pct
                trough_at_worst, peak_at_worst = balance, peak
                recover = 0
                for j in range(i + 1, len(curve)):
                    if curve[j][1] >= peak:
                        recover = j - peak_index
                        break
                else:
                    recover = -1
            streak = streak + 1 if pnl < 0 else 0
            worst_streak = max(worst_streak, streak)
        low = min(b for _w, b, _p in curve) if curve else args.capital
        rec = "never" if recover == -1 else f"{recover}"
        print(f"  {risk:>5.0%}{outcome['final_balance']:>13,.0f}"
              f"{outcome['total_return_pct']:>+10.0f}%{peak:>13,.0f}{low:>15,.0f}"
              f"{worst_dd_pct:>10.1f}%{worst_streak:>14}{rec:>19}", flush=True)
        curves[risk] = (curve, worst_dd_pct, peak_at_worst, trough_at_worst)

    print("\n  The max DD % column is the one that decides whether a level is tradeable:")
    print("  it is the share of everything accumulated that vanished at the worst moment.")
    for risk in (0.02, 0.10):
        _curve, dd, pk, tr = curves[risk]
        print(f"    at {risk:.0%} risk the account fell from {pk:,.0f} to {tr:,.0f} "
              f"-- losing {dd:.1f}% of its peak")

    year = args.detail_year
    print(f"\n\nYEAR {year} IN DETAIL -- monthly, at 2% and 10% risk")
    print("=" * 126)
    for risk in (0.02, 0.10):
        curve = curves[risk][0]
        rows = [(w, b, p) for w, b, p in curve if w.year == int(year)]
        if not rows:
            print(f"\n  {risk:.0%}: no trades in {year}")
            continue
        monthly: dict[str, list] = defaultdict(list)
        for when, balance, pnl in rows:
            monthly[when.strftime("%Y-%m")].append((balance, pnl))
        print(f"\n  risk {risk:.0%}")
        print(f"    {'month':<10}{'trades':>8}{'wins':>7}{'P&L':>14}{'end balance':>15}")
        for month in sorted(monthly):
            entries = monthly[month]
            pnl = sum(p for _b, p in entries)
            wins = sum(1 for _b, p in entries if p > 0)
            print(f"    {month:<10}{len(entries):>8}{wins:>7}{pnl:>+14,.0f}"
                  f"{entries[-1][0]:>15,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
