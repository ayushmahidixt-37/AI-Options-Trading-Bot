"""#5 Day-of-Week Effect, daily NIFTY, 2022-2023 (Top-10 #5) -- a transparent
DIAGNOSTIC, not a claimed reproduction of the cited strategy.

Executed via index points at NIFTY-futures cost economics, not real options
-- this archive's option chain only has priced data in each contract's last
~5-7 trading days before expiry, which cannot support a multi-week/month
hold; see engine.py's module docstring for the options convention used
everywhere else in this project. Cost/sizing machinery lives in
strategies/_swing_common.py.

WHY A DIAGNOSTIC, NOT A STRATEGY REPLICATION: strategies-and-parameters.md
#5's exact QQQ rule is explicitly paywalled/unpublished -- there is no rule
to implement faithfully. This script instead does two separate things:

  1. A DIAGNOSTIC TABLE (extra['diagnostic_table']): NIFTY's own mean daily
     return for buying at each weekday's close and selling N trading
     SESSIONS later (N=1,2,3,4), for every weekday x N combination, over
     2022-2023. Purely descriptive -- no costs, no sizing, no Trade objects.
  2. ONE trade signal that DOES generate real Trade objects and the
     headline metrics: buy at Monday's close, sell at Wednesday's close --
     a commonly-cited generic day-of-week pattern shape, chosen BEFORE
     looking at which cell of this script's own diagnostic table (1) came
     out best, precisely so this is not curve-fit to this same 2022-2023
     sample.

THIS RESULT IS NOT COMPARABLE to strategies-and-parameters.md #5's cited
76% win rate / 15% CAGR figures. That citation is for QuantifiedStrategies'
proprietary, paywalled QQQ rule. The rule tested here is a different,
generic pattern shape, run on a different market (NIFTY, not QQQ), chosen
independently of this backtest's own results. Do not treat the win
rate/CAGR below as validating or refuting that citation.

INDICATOR WARM-UP: 2x daily ATR(14) (see SIZING below) needs a short
run-up; index bars are loaded from 2020-01-01 (_swing_common.WARMUP_START)
as with every other script in this batch, for consistency.

FILL: the Monday-Wednesday rule fills AT session end both sides -- buy at
Monday's own last session-minute close, sell at Wednesday's own last
session-minute close (_swing_common.last_minute_fill), per the spec's exact
wording ("buy at Monday's close ... sell at Wednesday's close"). No
next-day lag is applied here (unlike t01-t04/t07), because the spec itself
already describes same-day close fills. Consequently held-to-dayend is
always None for this script's trades -- both legs already exit/enter at
day-end, exactly as instructed for scripts whose exit already IS session
end.

WEEK MATCHING (judgment call): a Monday/Wednesday pair is only taken if
BOTH exact calendar dates (Monday, Monday+2 days) have a real trading
session AND the Monday+2 session's weekday is actually Wednesday (guards
against a holiday-shifted session landing on the wrong day-name). If either
leg's exact date has no session (a market holiday), that week is skipped
entirely rather than shifted to the nearest trading day.

SIZING (judgment call -- no rule-defined stop for a fixed 2-day calendar
hold): risk distance is 2x daily ATR(14) at the Monday signal day, the same
convention used in t01/t02/t03/t06.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
import strategies._swing_common as C  # noqa: E402

NAME = "t05_day_of_week"
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]
HOLD_LENGTHS = (1, 2, 3, 4)
ATR_MULT = 2.0


def build_diagnostic_table(db, start_i):
    """Mean daily return of buying at each weekday's close and selling N
    trading SESSIONS later, N in HOLD_LENGTHS, over db[start_i:]."""
    table = {}
    n = len(db)
    for wd_idx, wd_name in enumerate(WEEKDAY_NAMES):
        for N in HOLD_LENGTHS:
            rets = []
            for i in range(start_i, n - N):
                if db[i]["date"].weekday() == wd_idx:
                    base = db[i]["close"]
                    if base > 0:
                        rets.append(db[i + N]["close"] / base - 1.0)
            wins = [r for r in rets if r > 0]
            key = f"{wd_name}_hold{N}d"
            table[key] = {
                "n": len(rets),
                "mean_return_pct": round(100 * C.mean(rets), 4) if rets else None,
                "win_rate_pct": round(100 * len(wins) / len(rets), 1) if rets else None,
            }
    return table


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=C.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars, db = C.load_daily(con)
    atr14 = C.daily_atr(db, 14)
    n = len(db)
    start_i = C.window_start_idx(db)

    diagnostic_table = build_diagnostic_table(db, start_i)

    trades, skips = [], {}

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    for i in range(start_i, n):
        if db[i]["date"].weekday() != 0:  # Monday
            continue
        wed_date = db[i]["date"] + timedelta(days=2)
        j = None
        # Wednesday must be the very next trading day after i for the pair
        # to represent the SAME calendar week's Mon->Wed move.
        if i + 1 < n and db[i + 1]["date"] == wed_date and db[i + 1]["date"].weekday() == 2:
            j = i + 1
        elif i + 2 < n and db[i + 2]["date"] == wed_date and db[i + 2]["date"].weekday() == 2:
            j = i + 2
        if j is None:
            skip("no_matching_wednesday")
            continue
        if atr14[i] is None:
            skip("no_atr")
            continue
        risk_points = ATR_MULT * atr14[i]
        qty = C.size_lots(E.CAPITAL, args.risk, risk_points)
        if qty <= 0:
            skip("qty_zero")
            continue
        entry_ts, entry_price = C.last_minute_fill(bars, db, i)
        exit_ts, exit_price = C.last_minute_fill(bars, db, j)
        tr = C.make_trade(NAME, "long", db, bars,
                           i, entry_ts, entry_price, j, exit_ts, exit_price,
                           "monday_close_to_wednesday_close", qty, risk_points,
                           slip=args.slippage,
                           notes=f"monday={db[i]['date']} wednesday={db[j]['date']}")
        trades.append(tr)

    out, payload = E.save_results(NAME, trades, extra={
        "skips": skips, "risk_pct": args.risk, "slippage": args.slippage,
        "diagnostic_table": diagnostic_table,
        "rule_tested": "buy Monday close, sell Wednesday close (generic pattern shape, "
                        "chosen independent of this backtest's own results)",
        "comparability_warning": (
            "This strategy's exact published rule is paywalled and unavailable. The rule "
            "tested here (buy Monday close, sell Wednesday close) is a commonly-cited generic "
            "day-of-week pattern shape chosen independently of this backtest's own results, "
            "NOT the QuantifiedStrategies proprietary rule the file's evidence citation refers "
            "to -- do not compare this result to that citation's 76% win rate / 15% CAGR "
            "figures as if it's the same rule."),
        "params": {"sizing_atr_mult": ATR_MULT, "warmup_start": C.WARMUP_START},
    })
    m = payload["metrics"]
    print(f"{NAME}: {len(trades)} trades (skips: {skips})")
    if m:
        print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
              f"PF {m['profit_factor']} | maxDD {m['max_drawdown_pct_of_peak']}% | "
              f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    else:
        print("  no trades")
    print(f"  diagnostic table cells: {len(diagnostic_table)} (see results json extra.diagnostic_table)")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
