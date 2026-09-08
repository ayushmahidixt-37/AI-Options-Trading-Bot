"""#3 Time-Series Momentum on NIFTY, monthly rebalance, 2022-2023 (Top-10 #3,
adapted).

Executed via index points at NIFTY-futures cost economics, not real options
-- this archive's option chain only has priced data in each contract's last
~5-7 trading days before expiry, which cannot support a multi-week/month
hold; see engine.py's module docstring for the options convention used
everywhere else in this project. Cost/sizing machinery lives in
strategies/_swing_common.py.

SUBSTITUTION (explicit, not a fabrication): the file's cited evidence is for
CROSS-SECTIONAL momentum -- rank many assets/countries, go long the top
decile, which is impossible with one instrument (NIFTY). This script
implements the well-documented TIME-SERIES sibling instead: go long NIFTY
when its own trailing 6-month return is positive, go flat when it is
negative, re-evaluated on the first trading day of each month. This is NOT
a claim to reproduce the cross-sectional/multi-country evidence cited in
strategies-and-parameters.md #3 -- only a test of the same underlying
"trend persists" idea on this one instrument.

INDICATOR WARM-UP: index bars are loaded from 2020-01-01
(_swing_common.WARMUP_START) so every monthly rebalance from 2022-01-01
onward already has a full trailing 6-month lookback available (the very
first rebalance needs data back to ~2021-07). No rebalance is acted on
before WINDOW_START_DATE.

TRAILING 6-MONTH RETURN: close on the rebalance day / close on the trading
day on-or-before (rebalance_date - ~182 calendar days) - 1. Approximated in
calendar days (not a fixed trading-day count), since NIFTY 6-month spans
cross variable numbers of trading days depending on holidays.

FILL: a rebalance decision is confirmed by the FIRST trading day of the
month's own close relative to 6 months prior; because that decision is only
knowable after that day's session, any resulting entry/exit fills at the
FIRST session-minute's close of the NEXT trading day
(_swing_common.first_minute_fill). Long or flat only -- never short.

SIZING (judgment call -- time-series momentum has no rule-defined stop):
risk distance is 2x daily ATR(14) at the rebalance signal day, same
convention as t01/t02/t05/t06. Sizing-only; exits still only happen on the
next rebalance's flip to a negative trailing return.

If a position is still open when the loaded window ends, it is
force-closed at the last available day's own last session-minute close,
reason "window_end_forced_close".
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
import strategies._swing_common as C  # noqa: E402

NAME = "t03_momentum"
LOOKBACK_DAYS = 182  # ~6 calendar months
ATR_MULT = 2.0


def month_start_indices(db):
    """Index of the first trading day of each calendar month."""
    out = []
    prev_month = None
    for i, d in enumerate(db):
        key = (d["date"].year, d["date"].month)
        if key != prev_month:
            out.append(i)
            prev_month = key
    return out


def trailing_return(db, i):
    """close[i] / close(on-or-before i's date - LOOKBACK_DAYS) - 1, or None
    if no bar exists that far back (shouldn't happen given the warm-up)."""
    target = db[i]["date"] - timedelta(days=LOOKBACK_DAYS)
    base_idx = None
    for j in range(i - 1, -1, -1):
        if db[j]["date"] <= target:
            base_idx = j
            break
    if base_idx is None:
        return None
    base_close = db[base_idx]["close"]
    if base_close <= 0:
        return None
    return db[i]["close"] / base_close - 1.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=C.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars, db = C.load_daily(con)
    atr14 = C.daily_atr(db, 14)
    n = len(db)
    rebalance_days = month_start_indices(db)

    trades, skips = [], {}

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    position = None  # dict when long is open
    for i in rebalance_days:
        if db[i]["date"] < E.WINDOW_START_DATE:
            continue
        tr_ret = trailing_return(db, i)
        if tr_ret is None:
            skip("no_lookback_bar")
            continue
        desired_long = tr_ret > 0
        fill_idx = i + 1

        if desired_long and position is None:
            if fill_idx >= n:
                skip("no_next_day_for_entry")
                continue
            if atr14[i] is None:
                skip("no_atr")
                continue
            risk_points = ATR_MULT * atr14[i]
            qty = C.size_lots(E.CAPITAL, args.risk, risk_points)
            if qty <= 0:
                skip("qty_zero")
                continue
            entry_ts, entry_price = C.first_minute_fill(bars, db, fill_idx)
            position = {
                "entry_idx": fill_idx, "entry_ts": entry_ts, "entry_price": entry_price,
                "qty": qty, "risk_points": risk_points,
                "signal_day": db[i]["date"], "signal_return_pct": round(100 * tr_ret, 2),
            }
        elif not desired_long and position is not None:
            if fill_idx >= n:
                exit_idx = n - 1
                exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
                reason = "window_end_forced_close"
            else:
                exit_idx = fill_idx
                exit_ts, exit_price = C.first_minute_fill(bars, db, exit_idx)
                reason = "trailing_return_turned_negative"
            tr = C.make_trade(NAME, "long", db, bars,
                               position["entry_idx"], position["entry_ts"], position["entry_price"],
                               exit_idx, exit_ts, exit_price, reason, position["qty"],
                               position["risk_points"], slip=args.slippage,
                               notes=f"entry_signal_day={position['signal_day']} "
                                     f"entry_6m_return={position['signal_return_pct']}% "
                                     f"exit_signal_day={db[i]['date']}")
            trades.append(tr)
            position = None
        # desired_long == currently-held state -> hold, no trade

    if position is not None:
        exit_idx = n - 1
        exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
        tr = C.make_trade(NAME, "long", db, bars,
                           position["entry_idx"], position["entry_ts"], position["entry_price"],
                           exit_idx, exit_ts, exit_price, "window_end_forced_close", position["qty"],
                           position["risk_points"], slip=args.slippage,
                           notes=f"entry_signal_day={position['signal_day']} still open at window end")
        trades.append(tr)

    out, payload = E.save_results(NAME, trades, extra={
        "skips": skips, "risk_pct": args.risk, "slippage": args.slippage,
        "n_rebalances_in_window": sum(1 for i in rebalance_days if db[i]["date"] >= E.WINDOW_START_DATE),
        "substitution_note": (
            "Time-series momentum on ONE instrument (NIFTY), NOT the cross-sectional/"
            "multi-country ranking the file's cited evidence measured. See docstring."),
        "params": {"lookback_days": LOOKBACK_DAYS, "sizing_atr_mult": ATR_MULT,
                    "warmup_start": C.WARMUP_START},
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
