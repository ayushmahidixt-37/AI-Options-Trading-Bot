"""Trade-by-trade comparison of FIXED vs ROLLING position sizing over one month.

Answers a specific question that the aggregate backtest cannot: given the
exact same signal sequence, how differently does a real account behave when
position size is pinned at one lot (what the live bot does today,
``MAX_LOTS_PER_TRADE=1``) versus sized from the current rolling balance?

Deliberately prints every trade rather than a summary -- the point is to see
*which* trades each regime takes, how many lots it commits, and where the two
diverge, not just the final number. Aggregates hide exactly the behaviour
being examined here.

No refitting, no parameter changes: the trade sequence comes from
``run_upstox_backtest`` using Candidate B's documented configuration, and both
regimes replay that identical sequence. The only difference between the two
columns is lot sizing.

Usage:
    python research/one_month_sizing_analysis.py \\
        --archive .termux-data/market-data.sqlite3 \\
        --start 2020-08-03 --end 2020-08-31 --starting-capital 100000
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

CANDIDATE_B_STRATEGY = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
CANDIDATE_B_PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def replay(trades, starting_capital, fee_per_order, fixed_lots, position_cap_pct):
    """Replay one trade sequence under one sizing regime.

    ``fixed_lots`` pins every trade to that many lots (the live bot's
    behaviour); ``None`` sizes from the rolling balance instead, capped by
    ``position_cap_pct`` of the balance.
    """
    balance = starting_capital
    rows = []
    for trade in sorted(trades, key=lambda t: t.entry_at):
        lot_size = trade.units
        premium_per_lot = trade.entry_price * lot_size
        if premium_per_lot <= 0:
            continue
        affordable = int((balance * position_cap_pct) // premium_per_lot)
        lots = min(affordable, fixed_lots) if fixed_lots else affordable
        if lots < 1:
            rows.append({"trade": trade, "lots": 0, "net": 0.0, "balance": balance, "skipped": True})
            continue
        units = lots * lot_size
        net = round(trade.raw_points * units - 2 * fee_per_order, 2)
        balance = round(balance + net, 2)
        rows.append({"trade": trade, "lots": lots, "net": net, "balance": balance, "skipped": False})
    return rows, balance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", default="2020-08-03")
    parser.add_argument("--end", default="2020-08-31")
    parser.add_argument("--starting-capital", type=float, default=100_000.0)
    parser.add_argument("--position-cap-pct", type=float, default=0.5)
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B_STRATEGY,
        start=date.fromisoformat(args.start), end=date.fromisoformat(args.end),
        settings=settings, parameters=CANDIDATE_B_PARAMS,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        include_dhan=True, include_derived=True,
    )
    trades = sorted(result.trade_details, key=lambda t: t.entry_at)
    print(f"Signal sequence {args.start} to {args.end}: {len(trades)} trades "
          f"(identical for both regimes; only lot sizing differs)\n")

    fixed_rows, fixed_final = replay(trades, args.starting_capital, settings.paper_fee_per_order, 1, args.position_cap_pct)
    roll_rows, roll_final = replay(trades, args.starting_capital, settings.paper_fee_per_order, None, args.position_cap_pct)

    print(f"{'Entry':<17}{'Dir':<8}{'Prem':>8}{'Exit why':<17}"
          f"{'| FIXED lots':>12}{'net':>10}{'balance':>12}"
          f"{'| ROLL lots':>12}{'net':>10}{'balance':>12}")
    print("-" * 118)
    for f, r in zip(fixed_rows, roll_rows):
        t = f["trade"]
        print(f"{t.entry_at.strftime('%Y-%m-%d %H:%M'):<17}{t.direction:<8}{t.entry_price:>8.2f}{t.exit_reason:<17}"
              f"{f['lots']:>12}{f['net']:>10.2f}{f['balance']:>12.2f}"
              f"{r['lots']:>12}{r['net']:>10.2f}{r['balance']:>12.2f}")

    def summarize(label, rows, final):
        taken = [x for x in rows if not x["skipped"]]
        lots = [x["lots"] for x in taken]
        peak = args.starting_capital
        worst = 0.0
        for x in rows:
            peak = max(peak, x["balance"])
            worst = max(worst, peak - x["balance"])
        print(f"{label:<10} final={final:>11.2f}  return={100*(final-args.starting_capital)/args.starting_capital:>7.2f}%  "
              f"taken={len(taken):>3}/{len(rows):<3} skipped={len(rows)-len(taken):>3}  "
              f"lots min/avg/max={min(lots) if lots else 0}/{sum(lots)/len(lots) if lots else 0:.1f}/{max(lots) if lots else 0}  "
              f"maxDD={worst:.2f}")

    print("\n" + "=" * 118)
    summarize("FIXED", fixed_rows, fixed_final)
    summarize("ROLLING", roll_rows, roll_final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
