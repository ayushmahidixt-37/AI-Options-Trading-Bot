"""Does an RSI conviction band fix the whipsaw failure? Tested day, next day, and beyond.

`day_anatomy.py` found that on 2021-03-02 the strategy fired six alternating
signals, every one with RSI between 45.1 and 54.1 -- readings that say "no
conviction in either direction" -- and lost all six. A band requiring RSI > 55
to buy calls and < 45 to buy puts would have blocked every one of them.

Two reasons this is worth testing rather than dismissing as another filter:
`BacktestParameters` already carries `bullish_rsi_min`/`bearish_rsi_max`, and an
earlier era of this project found "Strict RSI 55/45 + ATR floor 20" to be its
best hand-tuned candidate -- yet Candidate B, the configuration that was
confirmed and deployed, does not use it. The 86-configuration sweep in
`parameter_sweep.py` also never touched these thresholds, so this is a genuine
hole in that search rather than a re-run of it.

Reports, for each variant:

- 2021-03-02, the diagnosed day, to confirm the rule does what the anatomy says.
- 2021-03-03, the next day, because that was asked for directly.
- Full 2021, 2022, 2023-2024 and 2025-2026, because a rule derived from one day
  and confirmed on the next is a sample of one. **The multi-year columns are the
  only ones that carry evidential weight**; the two single days are included for
  inspection, not as proof.

Warm-up matters and is handled: running the engine on a single day starves the
60-period macro EMA of history and silently produces different signals from the
same day inside a longer run. Every figure here comes from one continuous run
per period, filtered afterwards by date.

Usage:
    python research/rsi_filter_study.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
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
VARIANTS = [
    ("baseline (no RSI band)", BASE),
    ("RSI 55/45", replace(BASE, bullish_rsi_min=55, bearish_rsi_max=45)),
    ("RSI 60/40", replace(BASE, bullish_rsi_min=60, bearish_rsi_max=40)),
    ("RSI 55/45 + ATR>=20", replace(BASE, bullish_rsi_min=55, bearish_rsi_max=45, minimum_atr=20)),
]
PERIODS = [
    ("2021 (development)", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022 (validation)", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023-2024 (held-out)", date(2023, 1, 1), date(2024, 10, 1)),
    ("2025-2026 (final unseen)", date(2025, 3, 1), date(2026, 8, 18)),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--day", default="2021-03-02")
    parser.add_argument("--next-day", default="2021-03-03")
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    focus = date.fromisoformat(args.day)
    nxt = date.fromisoformat(args.next_day)

    print(f"RSI conviction band -- Rs {args.capital:,.0f} rolling account, risk-sized 2%, real costs")
    print(f"Diagnosed day {focus} and the following day {nxt} are shown for inspection only;")
    print("the multi-year rows are what carry evidential weight.")
    print("=" * 122)

    day_rows: dict[str, tuple] = {}
    for period_name, start, end in PERIODS:
        print(f"\n{period_name}   ({start} .. {end})")
        for label, params in VARIANTS:
            result = run_upstox_backtest(
                archive, strategy=CANDIDATE_B, start=start, end=end, settings=settings,
                parameters=params, underlying_key=NIFTY_UNDERLYING_KEY,
                timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
            )
            if result.trades == 0:
                print(f"  {label:<26} (no trades)")
                continue
            outcome = simulate(result.trade_details, args.capital, settings.paper_fee_per_order,
                               None, 0.5, sizing="risk", risk_pct=0.02)
            print(f"  {label:<26} n={result.trades:>4} win={result.win_rate * 100:>4.1f}% "
                  f"pnl={result.net_pnl:>10,.0f} PF={result.profit_factor or 0:>5.2f} "
                  f"| Rs1L={outcome['final_balance']:>9,.0f} "
                  f"({outcome['total_return_pct']:>+6.1f}%) "
                  f"taken={outcome['trades_taken']}/{result.trades}")
            if start <= focus <= end:
                d1 = [t for t in result.trade_details if t.entry_at.date() == focus]
                d2 = [t for t in result.trade_details if t.entry_at.date() == nxt]
                day_rows[label] = (len(d1), sum(t.net_pnl for t in d1),
                                   len(d2), sum(t.net_pnl for t in d2))

    if day_rows:
        print("\n\nTHE TWO SINGLE DAYS (inspection only -- a sample of one proves nothing)")
        print("-" * 122)
        print(f"  {'variant':<26}{focus} trades{'':>3}{focus} P&L{'':>6}"
              f"{nxt} trades{'':>3}{nxt} P&L")
        for label, _p in VARIANTS:
            if label not in day_rows:
                continue
            n1, p1, n2, p2 = day_rows[label]
            print(f"  {label:<26}{n1:>12}{p1:>+16,.2f}{n2:>16}{p2:>+16,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
