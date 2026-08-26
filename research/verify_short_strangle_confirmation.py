"""Re-verify the short strangle's 2020-2024 confirmation, quarter by quarter.

Written 2026-08-26 after Candidate B's confirmation was found to be
unreproducible and retracted (see
`research/CANDIDATE_B_REPRODUCTION_INVESTIGATION.md`). That confirmation was
run ad hoc with only its output pasted into BACKTEST_FINDINGS.md, leaving
nothing to diff against when the numbers were challenged. The short
strangle's confirmation (claimed: 12 of 17 quarters profitable, +67,980 net
P&L) was produced the same way and is therefore equally unverified.

This script exists so that claim can be regenerated on demand rather than
trusted. It changes no parameters and refits nothing -- it replays the
documented configuration exactly, one call per quarter (the same
quarter-by-quarter discipline the confirmation claims to have used), and
prints a table directly comparable to the one in the findings log.

Usage:
    python research/verify_short_strangle_confirmation.py \\
        --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
from datetime import date

from options_bot.backtest_cli import _settings_for_archive
from options_bot.market_archive import MarketArchive
from options_bot.short_premium_backtest import (
    ShortStrangleParameters,
    run_short_strangle_backtest,
)
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

# The configuration BACKTEST_FINDINGS.md's 2026-08-25 entry says was confirmed.
CONFIRMED_PARAMS = ShortStrangleParameters(
    strike_distance_pct=0.002,
    stop_multiple=2.0,
    target_fraction=0.5,
    exclude_expiry_day=True,
    maximum_opening_range_pct=0.005,
    opening_range_bars=6,
)

# The claimed result, for direct comparison (12/17 quarters, +67,980 net).
CLAIMED_QUARTERS_PROFITABLE = 12
CLAIMED_NET_PNL = 67_980.0

QUARTERS: list[tuple[str, date, date]] = [
    ("2020-Q3 (partial)", date(2020, 8, 3), date(2020, 9, 30)),
    ("2020-Q4", date(2020, 10, 1), date(2020, 12, 31)),
    ("2021-Q1", date(2021, 1, 1), date(2021, 3, 31)),
    ("2021-Q2", date(2021, 4, 1), date(2021, 6, 30)),
    ("2021-Q3", date(2021, 7, 1), date(2021, 9, 30)),
    ("2021-Q4", date(2021, 10, 1), date(2021, 12, 31)),
    ("2022-Q1", date(2022, 1, 1), date(2022, 3, 31)),
    ("2022-Q2", date(2022, 4, 1), date(2022, 6, 30)),
    ("2022-Q3", date(2022, 7, 1), date(2022, 9, 30)),
    ("2022-Q4", date(2022, 10, 1), date(2022, 12, 31)),
    ("2023-Q1", date(2023, 1, 1), date(2023, 3, 31)),
    ("2023-Q2", date(2023, 4, 1), date(2023, 6, 30)),
    ("2023-Q3", date(2023, 7, 1), date(2023, 9, 30)),
    ("2023-Q4", date(2023, 10, 1), date(2023, 12, 31)),
    ("2024-Q1", date(2024, 1, 1), date(2024, 3, 31)),
    ("2024-Q2", date(2024, 4, 1), date(2024, 6, 30)),
    ("2024-Q3 (partial)", date(2024, 7, 1), date(2024, 10, 1)),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    parser.add_argument(
        "--no-costs", action="store_true",
        help="Pass settings=None to the engine, which zeroes fees and slippage. NOT a realistic "
             "configuration -- a real account pays both. Included only to test whether the "
             "claimed 2026-08-25 numbers were produced by an idealised, cost-free run.",
    )
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = None if args.no_costs else _settings_for_archive(args.archive)
    if args.no_costs:
        print("*** --no-costs: fees and slippage disabled. NOT a realistic result. ***")

    print(f"CLAIMED (BACKTEST_FINDINGS.md 2026-08-25): "
          f"{CLAIMED_QUARTERS_PROFITABLE}/17 quarters profitable, net P&L +{CLAIMED_NET_PNL:,.2f}")
    print("-" * 96)
    print(f"{'Quarter':<20}{'Trades':>8}{'Win rate':>10}{'Net P&L':>14}{'Drawdown':>12}{'Profit factor':>15}")
    print("-" * 96)

    total_trades = 0
    total_pnl = 0.0
    profitable = 0
    for label, start, end in QUARTERS:
        result = run_short_strangle_backtest(
            archive, start=start, end=end, settings=settings,
            parameters=CONFIRMED_PARAMS, underlying_key=args.underlying_key,
            timeframe=args.timeframe, include_dhan=True, include_derived=True,
        )
        total_trades += result.trades
        total_pnl += result.net_pnl
        if result.net_pnl > 0:
            profitable += 1
        pf = f"{result.profit_factor:.2f}" if result.profit_factor is not None else "--"
        win = f"{result.win_rate * 100:.1f}%" if result.trades else "--"
        print(f"{label:<20}{result.trades:>8}{win:>10}{result.net_pnl:>14,.2f}"
              f"{result.max_drawdown:>12,.2f}{pf:>15}")

    print("-" * 96)
    print(f"{'TOTAL':<20}{total_trades:>8}{'':>10}{total_pnl:>14,.2f}")
    print(f"\nQuarters profitable: {profitable}/17   (claimed {CLAIMED_QUARTERS_PROFITABLE}/17)")
    print(f"Net P&L:             {total_pnl:,.2f}   (claimed {CLAIMED_NET_PNL:,.2f})")

    pnl_ok = abs(total_pnl - CLAIMED_NET_PNL) < 0.01 * abs(CLAIMED_NET_PNL)
    quarters_ok = profitable == CLAIMED_QUARTERS_PROFITABLE
    if pnl_ok and quarters_ok:
        print("\nVERDICT: REPRODUCES -- the confirmation stands.")
    else:
        print("\nVERDICT: DOES NOT REPRODUCE -- treat the confirmation as invalid until explained.")
        print(f"         net P&L differs by {total_pnl - CLAIMED_NET_PNL:,.2f}; "
              f"profitable quarters differ by {profitable - CLAIMED_QUARTERS_PROFITABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
