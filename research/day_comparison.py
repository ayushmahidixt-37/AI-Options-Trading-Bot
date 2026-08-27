"""Side-by-side: one trading day under fixed-1-lot vs rolling capital.

The aggregate numbers say the fixed-1-lot backtest and the Rs 1,00,000 rolling
account disagree, but an aggregate cannot show *why*. This picks the single
worst day for the rolling account and prints every trade twice -- once as the
backtest scored it, once as the account actually experienced it -- so the
divergence is visible trade by trade rather than inferred.

Three things it is built to expose:

1. **Position sizing.** The backtest always takes exactly one lot. The rolling
   account takes as many as its sizing rule allows, so a single bad trade can
   be multiplied several times over.
2. **Skipped trades.** The backtest takes every signal; the account refuses any
   it cannot fund. Those refusals are invisible in the aggregate.
3. **A real accounting inconsistency, quantified per trade.** `simulate` scores
   a trade as `raw_points * units`, where `raw_points` is the unadjusted
   `exit_price - entry_open`. The backtest instead applies slippage to both
   fills. So the rolling account systematically *understates* costs relative to
   the engine it is fed by -- it is the more optimistic of the two, not the
   harsher one, which is the opposite of what the headline returns suggest.

Concurrency is deliberately not a variable here: `run_upstox_backtest` bounds
each trade's exit by the next signal's timestamp, so positions are strictly
sequential and never overlap. The account replays that same sequence in entry
order. Neither model holds two positions at once.

Usage:
    python research/day_comparison.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
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
PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2021-12-31")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-pct", type=float, default=0.02)
    parser.add_argument("--day", default=None, help="Force a specific YYYY-MM-DD instead of the worst.")
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B,
        start=date.fromisoformat(args.start), end=date.fromisoformat(args.end),
        settings=settings, parameters=PARAMS, underlying_key=NIFTY_UNDERLYING_KEY,
        timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
    )
    outcome = simulate(result.trade_details, args.capital, settings.paper_fee_per_order,
                       None, 0.5, sizing="risk", risk_pct=args.risk_pct)

    by_day_event: dict[str, list[dict]] = defaultdict(list)
    for event in outcome["events"]:
        by_day_event[event["entry_at"][:10]].append(event)
    daily_roll = {
        day: sum(e["net_pnl"] for e in evs if not e["skipped"])
        for day, evs in by_day_event.items()
    }
    trades_by_day: dict[str, list] = defaultdict(list)
    for trade in result.trade_details:
        trades_by_day[trade.entry_at.date().isoformat()].append(trade)
    daily_fixed = {d: sum(t.net_pnl for t in ts) for d, ts in trades_by_day.items()}

    if args.day:
        target = args.day
    else:
        target = min(daily_roll, key=lambda d: daily_roll[d])

    print(f"Period {args.start}..{args.end}   Rs {args.capital:,.0f} rolling, risk-sized {args.risk_pct:.0%}")
    print(f"Whole period: fixed-1-lot {result.net_pnl:>+12,.2f}   "
          f"rolling account {outcome['final_balance']:,.2f} "
          f"({outcome['total_return_pct']:+.1f}%), "
          f"{outcome['trades_taken']} taken / {outcome['trades_skipped_insufficient_capital']} skipped")
    print("=" * 128)
    print(f"\nWORST DAY FOR THE ROLLING ACCOUNT: {target}")
    print(f"  fixed-1-lot P&L that day : {daily_fixed.get(target, 0.0):>+12,.2f}")
    print(f"  rolling account P&L      : {daily_roll.get(target, 0.0):>+12,.2f}")
    print("=" * 128)

    events = {e["entry_at"]: e for e in by_day_event[target]}
    print(f"\n{'time':<9}{'symbol':<22}{'dir':<9}{'entry':>8}{'stop':>8}{'exit':>8}"
          f"{'reason':<17}{'1-lot P&L':>12}{'lots':>6}{'rolling P&L':>13}")
    print("-" * 128)
    fixed_total = roll_total = 0.0
    for trade in sorted(trades_by_day.get(target, []), key=lambda t: t.entry_at):
        event = events.get(trade.entry_at.isoformat())
        fixed_total += trade.net_pnl
        if event is None:
            lots_txt, roll_txt = "-", "(not in account)"
        elif event["skipped"]:
            lots_txt, roll_txt = "0", "SKIPPED"
        else:
            lots_txt = str(event["lots"])
            roll_total += event["net_pnl"]
            roll_txt = f"{event['net_pnl']:+,.2f}"
        print(f"{trade.entry_at.strftime('%H:%M'):<9}{trade.symbol:<22}{trade.direction:<9}"
              f"{trade.entry_price:>8.2f}{trade.stop_price:>8.2f}{trade.exit_price:>8.2f}"
              f"{trade.exit_reason:<17}{trade.net_pnl:>+12,.2f}{lots_txt:>6}{roll_txt:>13}")
    print("-" * 128)
    print(f"{'TOTAL':<73}{fixed_total:>+12,.2f}{'':>6}{roll_total:>+13,.2f}")

    # The accounting inconsistency, quantified on this day's trades.
    print("\n\nWHY THE PER-TRADE NUMBERS DIFFER EVEN AT ONE LOT")
    print("simulate() scores a trade as raw_points * units, where raw_points is the")
    print("UNADJUSTED exit-minus-entry. The backtest applies slippage to both fills.")
    print("At one lot the two should otherwise be identical, so any gap is pure slippage:\n")
    print(f"  {'time':<9}{'backtest net':>15}{'simulate net (1 lot)':>24}{'gap (slippage)':>18}")
    one_lot = simulate(trades_by_day.get(target, []), args.capital,
                       settings.paper_fee_per_order, 1, 0.5)
    one_lot_by_entry = {e["entry_at"]: e for e in one_lot["events"] if not e["skipped"]}
    gap_total = 0.0
    for trade in sorted(trades_by_day.get(target, []), key=lambda t: t.entry_at):
        event = one_lot_by_entry.get(trade.entry_at.isoformat())
        if event is None:
            continue
        gap = event["net_pnl"] - trade.net_pnl
        gap_total += gap
        print(f"  {trade.entry_at.strftime('%H:%M'):<9}{trade.net_pnl:>+15,.2f}"
              f"{event['net_pnl']:>+24,.2f}{gap:>+18,.2f}")
    print(f"  {'TOTAL GAP':<9}{'':>15}{'':>24}{gap_total:>+18,.2f}")
    print("\n  A positive gap means the rolling account credited MORE than the backtest --")
    print("  it is the more optimistic model, because it never charges slippage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
