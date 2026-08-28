"""Stage 1: quarterly breakdown, to choose which quarter to dissect at 1-minute resolution.

Prints every quarter twice -- fixed-1-lot and Rs 1,00,000 rolling (each quarter
starting fresh at Rs 1,00,000 so they are comparable to one another) -- for both
the current baseline and the RSI 60/40 conviction band, then names the best and
worst quarter for each.

One continuous backtest per variant, split by quarter afterwards. Running the
engine per-quarter would starve the 60-period macro EMA of warm-up and silently
produce different trades for the same dates -- an error that has already
appeared twice in this project.

Also reports 1-minute option-leg coverage per quarter, because the follow-up
analysis is only possible where both the underlying and the traded contracts
have real ONE_MINUTE candles.

Usage:
    python research/quarter_selector.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
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
RSI = replace(BASE, bullish_rsi_min=60, bearish_rsi_max=40)


def quarter_of(day: date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", default="2020-08-03")
    parser.add_argument("--end", default="2024-10-01")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--coverage", action="store_true",
                        help="Also scan ONE_MINUTE coverage per quarter (slow: full scans).")
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    print(f"Quarterly breakdown {start}..{end}   each quarter starts fresh at Rs {args.capital:,.0f}")
    print("=" * 122)

    per_variant: dict[str, dict[str, list]] = {}
    for label, params in (("baseline", BASE), ("RSI 60/40", RSI)):
        result = run_upstox_backtest(
            archive, strategy=CANDIDATE_B, start=start, end=end, settings=settings,
            parameters=params, underlying_key=NIFTY_UNDERLYING_KEY,
            timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
        )
        buckets: dict[str, list] = defaultdict(list)
        for trade in result.trade_details:
            buckets[quarter_of(trade.entry_at.date())].append(trade)
        per_variant[label] = buckets
        print(f"\n{label}: {result.trades} trades, net {result.net_pnl:+,.0f}")

    quarters = sorted(set(per_variant["baseline"]) | set(per_variant["RSI 60/40"]))
    print(f"\n{'quarter':<10}"
          f"{'BASE n':>8}{'BASE fixed':>13}{'BASE Rs1L':>12}"
          f"{'RSI n':>8}{'RSI fixed':>13}{'RSI Rs1L':>12}")
    print("-" * 122)
    summary: dict[str, dict] = {}
    for q in quarters:
        row = {"quarter": q}
        cells = []
        for label in ("baseline", "RSI 60/40"):
            trades = per_variant[label].get(q, [])
            if not trades:
                cells.append((0, 0.0, args.capital))
                continue
            fixed = sum(t.net_pnl for t in trades)
            outcome = simulate(trades, args.capital, settings.paper_fee_per_order,
                               None, 0.5, sizing="risk", risk_pct=0.02)
            cells.append((len(trades), fixed, outcome["final_balance"]))
        (bn, bf, br), (rn, rf, rr) = cells
        row.update({"base_n": bn, "base_fixed": bf, "base_roll": br,
                    "rsi_n": rn, "rsi_fixed": rf, "rsi_roll": rr})
        summary[q] = row
        print(f"{q:<10}{bn:>8}{bf:>+13,.0f}{br:>12,.0f}"
              f"{rn:>8}{rf:>+13,.0f}{rr:>12,.0f}", flush=True)

    def pick(key, fn):
        valid = [r for r in summary.values() if r["rsi_n"] > 0]
        return fn(valid, key=lambda r: r[key])

    worst_base = pick("base_fixed", min)
    best_base = pick("base_fixed", max)
    worst_rsi = pick("rsi_fixed", min)
    best_rsi = pick("rsi_fixed", max)
    print("\nSELECTED QUARTERS")
    print(f"  worst for baseline : {worst_base['quarter']}  "
          f"fixed {worst_base['base_fixed']:+,.0f}  Rs1L {worst_base['base_roll']:,.0f}")
    print(f"  best  for baseline : {best_base['quarter']}  "
          f"fixed {best_base['base_fixed']:+,.0f}  Rs1L {best_base['base_roll']:,.0f}")
    print(f"  worst for RSI 60/40: {worst_rsi['quarter']}  "
          f"fixed {worst_rsi['rsi_fixed']:+,.0f}  Rs1L {worst_rsi['rsi_roll']:,.0f}")
    print(f"  best  for RSI 60/40: {best_rsi['quarter']}  "
          f"fixed {best_rsi['rsi_fixed']:+,.0f}  Rs1L {best_rsi['rsi_roll']:,.0f}")

    print("\n\n1-MINUTE OPTION-LEG COVERAGE (needed for the day-level dissection)")
    print("-" * 122)
    with archive.connect() as con:
        for q in quarters:
            year, qq = int(q[:4]), int(q[-1])
            qs = date(year, (qq - 1) * 3 + 1, 1)
            qe = date(year + (1 if qq == 4 else 0), 1 if qq == 4 else qq * 3 + 1, 1)
            row = con.execute(
                """SELECT COUNT(*), COUNT(DISTINCT instrument_token),
                          SUM(CASE WHEN open_interest IS NOT NULL THEN 1 ELSE 0 END)
                   FROM market_candles mc
                   WHERE mc.timeframe='ONE_MINUTE' AND mc.source='dhan'
                     AND mc.started_at>=? AND mc.started_at<?""",
                (qs.isoformat(), qe.isoformat()),
            ).fetchone()
            print(f"  {q:<10} option 1-min rows={row[0]:>10,}  contracts={row[1]:>6,}  "
                  f"with OI={row[2] or 0:>10,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
