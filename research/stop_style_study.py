"""Fixed-rupee stop vs percentage-of-premium stop, under Rs 1,00,000 rolling capital.

Acts on a measurement, not a hunch. research/trade_postmortem.py found the
current fixed-rupee stop fires at a median MAE of -17.0%, while winners reach a
median MAE of only -6.0% against losers' -15.4%. The distributions separate
sharply: 84% of losers dip past -10% but only 24% of winners ever do. That says
the stop sits well past the level which actually discriminates, and that a
percentage stop nearer -10% should cut losers earlier while sparing most winners.

A fixed rupee budget also hands out wildly uneven percentage leashes -- a Rs 29
option gets -41%, a Rs 131 option -9% -- which is arithmetic, not a risk choice.

Every configuration is scored the way a real account experiences it: Rs 1,00,000
of rolling capital, risk-based sizing, real fees and slippage, trades skipped
when unaffordable. Dev/val/held-out are fixed before any result is inspected.

Usage:
    python research/stop_style_study.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date

from options_bot.backtest import BacktestParameters
from options_bot.backtest_cli import _settings_for_archive
from options_bot.market_archive import MarketArchive
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy
from options_bot.upstox_backtest import run_upstox_backtest
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

import sys as _sys  # noqa: E402
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from capital_compounding_simulation import simulate  # noqa: E402

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
BASE = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)
VARIANTS = [
    ("fixed-rupee (current)", BASE),
    ("pct stop 6%", replace(BASE, stop_loss_pct=0.06)),
    ("pct stop 8%", replace(BASE, stop_loss_pct=0.08)),
    ("pct stop 10%", replace(BASE, stop_loss_pct=0.10)),
    ("pct stop 12%", replace(BASE, stop_loss_pct=0.12)),
    ("pct stop 15%", replace(BASE, stop_loss_pct=0.15)),
    ("pct stop 20%", replace(BASE, stop_loss_pct=0.20)),
]


def run(archive, settings, params, start, end, capital):
    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B, start=start, end=end, settings=settings,
        parameters=params, underlying_key=NIFTY_UNDERLYING_KEY,
        timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
    )
    outcome = simulate(
        result.trade_details, capital, settings.paper_fee_per_order,
        None, 0.5, sizing="risk", risk_pct=0.02,
    )
    return result, outcome


def show(label, result, outcome):
    print(f"  {label:<24} raw_pnl={result.net_pnl:>11,.0f}  win={result.win_rate * 100:>4.1f}%  "
          f"|  Rs1L final={outcome['final_balance']:>10,.0f}  "
          f"({outcome['total_return_pct']:>+6.1f}%)  DD={outcome['max_drawdown']:>9,.0f}  "
          f"taken={outcome['trades_taken']}/{outcome['trades_taken'] + outcome['trades_skipped_insufficient_capital']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--capital", type=float, default=100_000.0)
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    windows = [
        ("DEVELOPMENT 2021", date(2021, 1, 1), date(2021, 12, 31)),
        ("VALIDATION  2022", date(2022, 1, 1), date(2022, 12, 31)),
        ("HELD-OUT    2023-2024", date(2023, 1, 1), date(2024, 10, 1)),
    ]
    print(f"Stop style study -- Rs {args.capital:,.0f} rolling capital, risk-sized 2%, real costs")
    print("=" * 118)
    for name, start, end in windows:
        print(f"\n{name}  ({start} .. {end})")
        for label, params in VARIANTS:
            result, outcome = run(archive, settings, params, start, end, args.capital)
            show(label, result, outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
