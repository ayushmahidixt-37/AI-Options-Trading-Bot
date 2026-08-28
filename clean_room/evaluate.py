"""Evaluate the frozen strategy in STRATEGY.md against a market archive.

Self-contained: point it at a database and a date range and it reports the
result. It deliberately offers **no parameters to tune** -- every strategy value
is hard-coded from STRATEGY.md, so an evaluation run cannot quietly become a
parameter search. Only the data source and the reporting window can be chosen.

Refuses to run against dates the strategy was derived from unless explicitly
overridden, and says so loudly when overridden, because a clean-room result on
contaminated data is worse than no result -- it looks like evidence.

Read PROTOCOL.md first. Record every run in RESULTS.md.

Usage:
    python clean_room/evaluate.py --archive path/to/market-data.sqlite3 \\
        --start 2026-08-21 --end 2026-12-31
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "research"))

from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import run_upstox_backtest  # noqa: E402
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

from capital_compounding_simulation import simulate  # noqa: E402

# --- FROZEN. See STRATEGY.md. Do not edit to make a result look better. -------
STRATEGY = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
PARAMETERS = BacktestParameters(
    bullish_rsi_min=60,
    bearish_rsi_max=40,
    minimum_option_premium=20,
    minimum_open_interest=100_000,
    stop_risk_fraction=1.6,
    target_return=0.30,
)
TIMEFRAME = "FIVE_MINUTE"
STARTING_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.02
POSITION_CAP = 0.5
# --- end frozen block ---------------------------------------------------------

CONTAMINATED_UNTIL = date(2026, 8, 20)  # every date up to here was used in development

CRITERIA = [
    ("sample >= 40 trades", lambda m: m["trades"] >= 40),
    ("net P&L > 0", lambda m: m["net_pnl"] > 0),
    ("win rate >= 33%", lambda m: m["win_rate"] >= 33.0),
    ("profit factor >= 1.15", lambda m: (m["profit_factor"] or 0) >= 1.15),
    ("max drawdown <= 35% of peak", lambda m: m["max_dd_pct"] <= 35.0),
    ("longest losing streak <= 20", lambda m: m["worst_streak"] <= 20),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    parser.add_argument("--include-dhan", action="store_true")
    parser.add_argument("--markdown", action="store_true",
                        help="Also print a copy-pasteable markdown block of the result.")
    parser.add_argument(
        "--i-know-this-data-is-contaminated", action="store_true",
        help="Permit a run whose range overlaps the development archive. The result "
             "is not a clean-room result and must be recorded as such.",
    )
    args = parser.parse_args(argv)

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    contaminated = start <= CONTAMINATED_UNTIL
    if contaminated and not args.i_know_this_data_is_contaminated:
        print(f"REFUSED: {start} falls on or before {CONTAMINATED_UNTIL}, which the strategy "
              f"was derived from.\nA clean-room evaluation needs data after that date. "
              f"Pass --i-know-this-data-is-contaminated to override,\nand record the run as "
              f"contaminated in RESULTS.md if you do.")
        return 2
    if contaminated:
        print("*" * 78)
        print("* WARNING: this range overlaps the development archive.                      *")
        print("* This is NOT a clean-room result. Record it as contaminated.                *")
        print("*" * 78)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    if settings.paper_fee_per_order <= 0 or settings.paper_slippage_bps <= 0:
        print(f"REFUSED: costs are switched off (fee={settings.paper_fee_per_order}, "
              f"slippage={settings.paper_slippage_bps}bps). See STRATEGY.md -- an earlier "
              f"result was inflated sevenfold by exactly this.")
        return 2

    print(f"Clean-room evaluation  {start} .. {end}")
    print(f"archive={args.archive}")
    print(f"fee={settings.paper_fee_per_order}/order  slippage={settings.paper_slippage_bps}bps")
    print("=" * 78, flush=True)

    result = run_upstox_backtest(
        archive, strategy=STRATEGY, start=start, end=end, settings=settings,
        parameters=PARAMETERS, underlying_key=args.underlying_key,
        timeframe=TIMEFRAME, include_dhan=args.include_dhan, include_derived=True,
    )
    if result.trades == 0:
        print("\nNo trades. Check the archive covers this range with option data.")
        return 1

    outcome = simulate(result.trade_details, STARTING_CAPITAL, settings.paper_fee_per_order,
                       None, POSITION_CAP, sizing="risk", risk_pct=RISK_PER_TRADE)
    taken = [e for e in outcome["events"] if not e["skipped"]]
    peak = STARTING_CAPITAL
    max_dd_pct = 0.0
    streak = worst_streak = 0
    for event in taken:
        balance = event["balance_after"]
        peak = max(peak, balance)
        max_dd_pct = max(max_dd_pct, (peak - balance) / peak * 100 if peak else 0.0)
        streak = streak + 1 if event["net_pnl"] < 0 else 0
        worst_streak = max(worst_streak, streak)

    metrics = {
        "trades": result.trades,
        "net_pnl": result.net_pnl,
        "win_rate": result.win_rate * 100,
        "profit_factor": result.profit_factor,
        "max_dd_pct": max_dd_pct,
        "worst_streak": worst_streak,
    }

    print(f"\n  trades taken            {outcome['trades_taken']} "
          f"(skipped for capital: {outcome['trades_skipped_insufficient_capital']})")
    print(f"  net P&L (fixed 1 lot)   {result.net_pnl:>+12,.2f}")
    print(f"  win rate                {metrics['win_rate']:>11.1f}%")
    print(f"  profit factor           {result.profit_factor if result.profit_factor else 0:>12.2f}")
    print(f"  Rs 1,00,000 @ 2% risk   {outcome['final_balance']:>12,.2f} "
          f"({outcome['total_return_pct']:+.1f}%)")
    print(f"  max drawdown            {max_dd_pct:>11.1f}% of peak")
    print(f"  longest losing streak   {worst_streak:>12}")
    if taken:
        pnls = [e["net_pnl"] for e in taken]
        print(f"  median trade            {statistics.median(pnls):>+12,.2f}")

    print("\n  PRE-REGISTERED CRITERIA (all six must hold)")
    passed = 0
    for name, test in CRITERIA:
        ok = test(metrics)
        passed += ok
        print(f"    [{'PASS' if ok else 'FAIL'}]  {name}")
    verdict = ("CONFIRMED -- all criteria met on unseen data" if passed == len(CRITERIA)
               else f"NOT CONFIRMED -- {passed}/{len(CRITERIA)} criteria met")
    if contaminated:
        verdict += "   (CONTAMINATED RANGE -- carries no evidential weight)"
    print(f"\n  VERDICT: {verdict}")
    print(f"\n  Record this run in RESULTS.md. Generated {datetime.now():%Y-%m-%d %H:%M}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
