"""#6 Turnaround Tuesday, daily NIFTY, 2022-2023 (Top-10 #6, adapted SPY -> NIFTY).

Executed via index points at NIFTY-futures cost economics, not real options
-- this archive's option chain only has priced data in each contract's last
~5-7 trading days before expiry, which cannot support a multi-week/month
hold; see engine.py's module docstring for the options convention used
everywhere else in this project. Cost/sizing machinery lives in
strategies/_swing_common.py.

RULE: strategies-and-parameters.md #6's exact numeric trigger is paywalled,
but the general PATTERN SHAPE is widely, publicly documented (unlike #5's
QQQ rule, which is proprietary end to end): if Monday's session closes DOWN
(Monday's daily close < Monday's daily open), buy at Monday's close (or the
nearest available 1-min fill just before session end); exit at Tuesday's
close. That literal, commonly-published version is implemented here, run on
NIFTY instead of SPY.

COMPARABILITY WARNING (same caveat as t05): do not compare this result to
the file's cited 75% win rate figure. That citation is for the exact SPY
rule with its numeric trigger, which is paywalled/unverified here. This
script runs the general pattern shape on a different market (SPY -> NIFTY)
with the exact trigger threshold unverified against the original source.

INDICATOR WARM-UP: 2x daily ATR(14) (see SIZING) needs a short run-up;
index bars loaded from 2020-01-01 (_swing_common.WARMUP_START), same as
every other script in this batch.

FILL: "buy at Monday's close (or the nearest available 1-min fill just
before session end)" -> Monday's own LAST session-minute close
(_swing_common.last_minute_fill). Exit "at Tuesday's close" -> Tuesday's
own last session-minute close, same function. Both legs already fill at
day-end, so held-to-dayend is always None here, as instructed for scripts
whose exit already IS session end.

TUESDAY MATCHING (judgment call): only Mondays immediately followed by a
trading session dated Monday+1 whose weekday is Tuesday are eligible -- if
Tuesday is a market holiday (next session lands on Wednesday), that week is
skipped rather than substituting Wednesday.

SIZING (judgment call -- no rule-defined stop for a fixed 1-day hold): risk
distance is 2x daily ATR(14) at the Monday signal day, same convention as
t01/t02/t03/t05.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
import strategies._swing_common as C  # noqa: E402

NAME = "t06_turnaround_tuesday"
ATR_MULT = 2.0


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

    trades, skips = [], {}

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    for i in range(start_i, n):
        if db[i]["date"].weekday() != 0:  # Monday
            continue
        monday_down = db[i]["close"] < db[i]["open"]
        if not monday_down:
            continue
        tue_date = db[i]["date"] + timedelta(days=1)
        if i + 1 >= n or db[i + 1]["date"] != tue_date or db[i + 1]["date"].weekday() != 1:
            skip("no_matching_tuesday")
            continue
        j = i + 1
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
                           "monday_close_to_tuesday_close", qty, risk_points,
                           slip=args.slippage,
                           notes=f"monday={db[i]['date']} (open={round(db[i]['open'],2)} "
                                 f"close={round(db[i]['close'],2)}) tuesday={db[j]['date']}")
        trades.append(tr)

    out, payload = E.save_results(NAME, trades, extra={
        "skips": skips, "risk_pct": args.risk, "slippage": args.slippage,
        "comparability_warning": (
            "Exact SPY trigger is paywalled/unverified. This runs the general 'buy weak "
            "Monday close, sell Tuesday close' pattern shape on NIFTY -- do not compare to "
            "the file's cited 75% win rate / 7.9% CAGR as if it's the same rule."),
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
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
