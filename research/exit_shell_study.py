"""Does Candidate B's entry signal have an edge that its exit shell throws away?

Motivated by a consistent pattern in the 2026-08-26 retraction investigation:
across 27 exit configurations, every variant that **capped** winners
(``target_return`` set) lost money, while every variant that let winners run
(``target_return=None``) was net positive (+4,996 to +7,774). That was a
single-window observation and therefore only a hypothesis -- it is exactly the
kind of "best cell in a sweep" result this project's own discipline exists to
distrust. This script tests it properly instead of acting on it.

Design (dev -> val -> held-out, chosen before looking at any result):

1. **Development** (2021-01-01..2022-12-31): sweep 10 exit shells. Nothing is
   selected on this range except the shortlist.
2. **Validation** (2023-01-01..2024-10-01): re-run the top 3 by development
   net P&L. If the ordering does not survive, that is itself the answer.
3. **Held-out** (2026-06-01..2026-08-20): the single best validation config,
   checked exactly once.

On the held-out range's status -- stated plainly because it matters:
`range_usage` shows the Candidate B family has consumed 2024-10-03 through
2026-05-31 (screening/development/validation), and the 2020-2024 range was
spent on the retracted confirmation. 2026-06-01..2026-08-20 is the only window
this strategy has never touched. It **cannot be registered as a formal TEST
range** -- the ledger requires a test range to start strictly after every range
ever recorded, and an existing test row already runs to 2026-08-20, where the
data also ends. So this is a genuine held-out check but not a ledger-certified
one, and no result here may be labelled "Confirmed". It is ~2.5 months, which
is a small sample; treat it as a disqualifier if it fails, not a certificate if
it passes.

The entry signal, contract selection and every entry filter are held **fixed**
at Candidate B's documented configuration throughout. Only the exit shell
varies -- that is the whole point.

Usage:
    python research/exit_shell_study.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import time
from dataclasses import replace
from datetime import date

from options_bot.backtest import BacktestParameters
from options_bot.backtest_cli import _settings_for_archive
from options_bot.market_archive import MarketArchive
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy
from options_bot.upstox_backtest import run_upstox_backtest
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

STRATEGY = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
# Entry side held fixed at the documented configuration; only exits vary.
BASE = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)

DEV = (date(2021, 1, 1), date(2022, 12, 31))
VAL = (date(2023, 1, 1), date(2024, 10, 1))
HELD_OUT = (date(2026, 6, 1), date(2026, 8, 20))

# (label, target_return, trailing_stop, trailing_activation_return)
CELLS: list[tuple[str, float | None, float | None, float | None]] = [
    ("baseline target=0.30", 0.30, None, None),
    ("uncapped (no target)", None, None, None),
    ("target=0.50", 0.50, None, None),
    ("target=0.80", 0.80, None, None),
    ("trail 0.20", None, 0.20, None),
    ("trail 0.30", None, 0.30, None),
    ("trail 0.40", None, 0.40, None),
    ("trail 0.30 act 0.20", None, 0.30, 0.20),
    ("trail 0.40 act 0.20", None, 0.40, 0.20),
    ("trail 0.40 act 0.30", None, 0.40, 0.30),
]


def params_for(cell) -> BacktestParameters:
    _label, target, trail, act = cell
    return replace(
        BASE, target_return=target, trailing_stop=trail, trailing_activation_return=act,
    )


def evaluate(archive, settings, params, start, end, underlying_key, timeframe) -> dict:
    result = run_upstox_backtest(
        archive, strategy=STRATEGY, start=start, end=end, settings=settings,
        parameters=params, underlying_key=underlying_key, timeframe=timeframe,
        include_dhan=True, include_derived=True,
    )
    wins = [t.net_pnl for t in result.trade_details if t.net_pnl > 0]
    losses = [t.net_pnl for t in result.trade_details if t.net_pnl <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "trades": result.trades,
        "win_rate": (len(wins) / result.trades * 100) if result.trades else 0.0,
        "net_pnl": result.net_pnl,
        "profit_factor": result.profit_factor,
        "roi": result.return_on_capital_pct,
        "max_drawdown": result.max_drawdown,
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": gross_loss / len(losses) if losses else 0.0,
    }


def show(label: str, m: dict) -> None:
    pf = f"{m['profit_factor']:.2f}" if m["profit_factor"] is not None else "--"
    roi = f"{m['roi']:.2f}%" if m["roi"] is not None else "--"
    print(f"  {label:<24} n={m['trades']:>4} win={m['win_rate']:>5.1f}% "
          f"net={m['net_pnl']:>11,.2f} PF={pf:>5} ROI={roi:>8} "
          f"DD={m['max_drawdown']:>10,.2f} avgW={m['avg_win']:>7.0f} avgL={m['avg_loss']:>6.0f}",
          flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    ctx = (args.underlying_key, args.timeframe)

    print("HYPOTHESIS: letting winners run beats capping them at +30%.")
    print(f"DEV {DEV[0]}..{DEV[1]}   VAL {VAL[0]}..{VAL[1]}   HELD-OUT {HELD_OUT[0]}..{HELD_OUT[1]}")
    print("=" * 118)

    print(f"\nPHASE 1 -- DEVELOPMENT sweep ({len(CELLS)} exit shells)")
    t0 = time.time()
    dev_results = []
    for index, cell in enumerate(CELLS, 1):
        metrics = evaluate(archive, settings, params_for(cell), *DEV, *ctx)
        dev_results.append((cell, metrics))
        show(cell[0], metrics)
        elapsed = time.time() - t0
        print(f"     [{index}/{len(CELLS)} done, {elapsed:.0f}s elapsed, "
              f"ETA {elapsed / index * (len(CELLS) - index):.0f}s]", flush=True)

    shortlist = sorted(dev_results, key=lambda r: r[1]["net_pnl"], reverse=True)[:3]
    print(f"\nShortlist by development net P&L: {[c[0] for c, _ in shortlist]}")

    print("\nPHASE 2 -- VALIDATION of the top 3")
    val_results = []
    for cell, _dev in shortlist:
        metrics = evaluate(archive, settings, params_for(cell), *VAL, *ctx)
        val_results.append((cell, metrics))
        show(cell[0], metrics)

    baseline_val = evaluate(archive, settings, params_for(CELLS[0]), *VAL, *ctx)
    show("(baseline, reference)", baseline_val)

    best_cell, best_val = max(val_results, key=lambda r: r[1]["net_pnl"])
    print(f"\nBest on validation: {best_cell[0]}")

    print(f"\nPHASE 3 -- HELD-OUT check, one shot: {best_cell[0]}")
    held = evaluate(archive, settings, params_for(best_cell), *HELD_OUT, *ctx)
    show(best_cell[0], held)
    held_baseline = evaluate(archive, settings, params_for(CELLS[0]), *HELD_OUT, *ctx)
    show("(baseline, reference)", held_baseline)

    print("\n" + "=" * 118)
    print("VERDICT INPUTS (interpret against the docstring's stated limits):")
    print(f"  candidate            : {best_cell[0]}")
    dev_by_label = {c[0]: m for c, m in dev_results}
    print(f"  dev net P&L          : {dev_by_label[best_cell[0]]['net_pnl']:,.2f}")
    print(f"  val net P&L          : {best_val['net_pnl']:,.2f}   (baseline {baseline_val['net_pnl']:,.2f})")
    print(f"  held-out net P&L     : {held['net_pnl']:,.2f}   (baseline {held_baseline['net_pnl']:,.2f})")
    beats = held["net_pnl"] > held_baseline["net_pnl"] and held["net_pnl"] > 0
    print(f"  beats baseline AND is positive on held-out: {'YES' if beats else 'NO'}")
    if not beats:
        print("  -> hypothesis NOT supported. Do not adopt.")
    else:
        print("  -> hypothesis survives, on a ~2.5 month sample that cannot be ledger-certified.")
        print("     Label Exploratory/Open at most; this is not a confirmation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
