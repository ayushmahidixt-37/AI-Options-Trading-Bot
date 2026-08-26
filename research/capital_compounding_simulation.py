"""What would a real, fixed ₹20,000 account actually have made trading
Candidate B, month by month, across its confirmed 2020-2024 fresh range?

Every existing backtest in this project (``run_upstox_backtest`` and
friends) reports ``return_on_capital_pct`` as net P&L divided by total
capital *turned over* across the whole run -- useful for comparing
strategies, but it is not what a single real account holder experiences,
because every trade in those engines is sized at a fixed one lot,
independent of how much capital is actually available or how much the
account has grown or shrunk so far. This script takes the real, already-
confirmed trade sequence (same strategy, same parameters, same range as
BACKTEST_FINDINGS.md's 2026-08-24 "Candidate B confirmed on fresh
2020-2024 data" entry -- nothing here is refit or re-tuned) and replays
it against a single rolling account balance, sizing each trade by what
that balance can actually afford at that moment, compounding wins and
losses forward exactly like a real account would.

Two modes, both real, showing two different questions:

``--max-lots 1`` (the live bot's actual configured ceiling,
``MAX_LOTS_PER_TRADE`` default) -- what the bot would really do today,
unchanged. Position size never grows even if the account does.

``--max-lots 0`` (uncapped) -- position size scales with the account
balance, capped only by ``--position-cap-pct`` (never commit more than
this fraction of current balance to one trade's premium -- a deliberate,
clearly-labelled diversification choice this script makes, not a
previously-validated parameter) and by what the balance can actually
afford. This is the "real compounding" scenario.

A trade whose cheapest single lot costs more than the affordable capital
is skipped entirely (recorded, not silently dropped) -- the same
fail-closed principle as every entry filter elsewhere in this project.

Usage:
    python research/capital_compounding_simulation.py \\
        --archive .termux-data/market-data.sqlite3 \\
        --starting-capital 20000 --max-lots 1
    python research/capital_compounding_simulation.py \\
        --archive .termux-data/market-data.sqlite3 \\
        --starting-capital 20000 --max-lots 0 --position-cap-pct 0.5
"""

from __future__ import annotations

import argparse
import json
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
CONFIRMED_START = date(2020, 8, 3)
CONFIRMED_END = date(2024, 10, 1)


def simulate(
    trades,
    starting_capital: float,
    fee_per_order: float,
    max_lots: int | None,
    position_cap_pct: float,
) -> dict:
    balance = starting_capital
    peak = balance
    max_drawdown = 0.0
    trade_events: list[dict] = []
    skipped = 0

    for trade in sorted(trades, key=lambda t: t.entry_at):
        lot_size = trade.units  # every source trade is exactly one lot
        premium_per_lot = trade.entry_price * lot_size
        if premium_per_lot <= 0:
            continue
        affordable_lots = int((balance * position_cap_pct) // premium_per_lot)
        lots = min(affordable_lots, max_lots) if max_lots else affordable_lots
        if lots < 1:
            skipped += 1
            trade_events.append(
                {
                    "entry_at": trade.entry_at.isoformat(),
                    "exit_at": trade.exit_at.isoformat(),
                    "symbol": trade.symbol,
                    "skipped": True,
                    "reason": f"balance {balance:.2f} can't afford one lot at premium {premium_per_lot:.2f}",
                }
            )
            continue
        units = lots * lot_size
        gross = round(trade.raw_points * units, 2)
        fees = round(2 * fee_per_order, 2)
        net = round(gross - fees, 2)
        balance_before = balance
        balance = round(balance + net, 2)
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)
        trade_events.append(
            {
                "entry_at": trade.entry_at.isoformat(),
                "exit_at": trade.exit_at.isoformat(),
                "symbol": trade.symbol,
                "direction": trade.direction,
                "lots": lots,
                "units": units,
                "net_pnl": net,
                "balance_before": balance_before,
                "balance_after": balance,
                "skipped": False,
            }
        )

    monthly: dict[str, dict] = {}
    for event in trade_events:
        month = event["exit_at"][:7]
        bucket = monthly.setdefault(
            month,
            {"trades": 0, "skipped": 0, "net_pnl": 0.0, "start_balance": None, "end_balance": None},
        )
        if event["skipped"]:
            bucket["skipped"] += 1
            continue
        bucket["trades"] += 1
        bucket["net_pnl"] = round(bucket["net_pnl"] + event["net_pnl"], 2)
        if bucket["start_balance"] is None:
            bucket["start_balance"] = event["balance_before"]
        bucket["end_balance"] = event["balance_after"]

    return {
        "starting_capital": starting_capital,
        "final_balance": balance,
        "total_return_pct": round((balance - starting_capital) / starting_capital * 100, 2),
        "max_drawdown": round(max_drawdown, 2),
        "trades_taken": sum(1 for e in trade_events if not e["skipped"]),
        "trades_skipped_insufficient_capital": skipped,
        "monthly": monthly,
        "events": trade_events,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--starting-capital", type=float, default=20_000.0)
    parser.add_argument(
        "--max-lots", type=int, default=1,
        help="0 = uncapped (position size scales with balance). 1 (default) matches "
             "the live bot's actual MAX_LOTS_PER_TRADE ceiling -- what it would really do today.",
    )
    parser.add_argument(
        "--position-cap-pct", type=float, default=0.5,
        help="Never commit more than this fraction of current balance to one trade's "
             "premium, even if affordable -- a deliberate diversification choice this "
             "script makes, not a previously-validated parameter.",
    )
    parser.add_argument("--start", default=CONFIRMED_START.isoformat())
    parser.add_argument("--end", default=CONFIRMED_END.isoformat())
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    print(f"running Candidate B's confirmed configuration {args.start} to {args.end} ...", flush=True)
    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B_STRATEGY, start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end), settings=settings, parameters=CANDIDATE_B_PARAMS,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
    )
    print(f"  {result.trades} confirmed trades, fixed-1-lot net_pnl={result.net_pnl:.2f}", flush=True)

    outcome = simulate(
        result.trade_details, args.starting_capital, settings.paper_fee_per_order,
        args.max_lots or None, args.position_cap_pct,
    )

    print(
        f"\nStarting capital: {outcome['starting_capital']:.2f}\n"
        f"Final balance:    {outcome['final_balance']:.2f}\n"
        f"Total return:     {outcome['total_return_pct']:.2f}%\n"
        f"Max drawdown:     {outcome['max_drawdown']:.2f}\n"
        f"Trades taken:     {outcome['trades_taken']}\n"
        f"Trades skipped (insufficient capital): {outcome['trades_skipped_insufficient_capital']}\n"
    )
    print(f"{'Month':<10}{'Trades':>8}{'Skipped':>9}{'Net P&L':>14}{'Start bal':>14}{'End bal':>14}{'Return %':>10}")
    for month, bucket in sorted(outcome["monthly"].items()):
        if bucket["start_balance"] is None:
            print(f"{month:<10}{bucket['trades']:>8}{bucket['skipped']:>9}  (no trades taken this month)")
            continue
        month_return = (bucket["end_balance"] - bucket["start_balance"]) / bucket["start_balance"] * 100
        print(
            f"{month:<10}{bucket['trades']:>8}{bucket['skipped']:>9}{bucket['net_pnl']:>14.2f}"
            f"{bucket['start_balance']:>14.2f}{bucket['end_balance']:>14.2f}{month_return:>9.2f}%"
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(outcome, handle, indent=2)
        print(f"\nfull detail written to {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
