"""#1 Golden Cross -- 50/200 SMA crossover, daily NIFTY, 2022-2023 (Top-10 #1).

Executed via index points at NIFTY-futures cost economics, not real options
-- this archive's option chain only has priced data in each contract's last
~5-7 trading days before expiry, which cannot support a multi-week/month
hold; see engine.py's module docstring for the options convention used
everywhere else in this project. Cost/sizing machinery lives in
strategies/_swing_common.py (module docstring there has the full rationale).

RULE: daily 50-SMA and 200-SMA of NIFTY close. Long when 50 crosses above
200; exit flat (never short) when it crosses back below. Very low frequency
by design (~1 trade/2yr in the original US evidence) -- this script reports
whatever the 2022-2023 window actually produces, including a 0- or 1-trade
result, without forcing more activity than the rule generates.

INDICATOR WARM-UP: SMA200 needs 200 trading days of run-up. Index bars are
loaded from 2020-01-01 (see _swing_common.WARMUP_START) purely so the
50/200 SMAs are already warmed up by 2022-01-01 -- no trade is ever entered
on a signal dated before WINDOW_START_DATE.

FILL: the crossover is confirmed by a day's daily close. The position is
entered/exited at the FIRST session-minute's close of the NEXT trading day
(_swing_common.first_minute_fill) -- a real, tradable 1-minute price, not
the already-closed crossover-day print.

SIZING (judgment call -- Golden Cross has no rule-defined stop): risk
distance for the capital-risk-% formula is 2x daily ATR(14) at the
crossover-confirmation day, a standard trend-following stop proxy. This is
for POSITION SIZING ONLY; the strategy still only exits on the SMA
cross-down signal, never on this ATR distance being hit.

If a position is still open at the end of the loaded window (2023-12-29),
it is force-closed at that day's own last session-minute close, reason
"window_end_forced_close" -- not a real signal exit, flagged as such.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
import strategies._swing_common as C  # noqa: E402

from options_bot.indicators import sma  # noqa: E402

NAME = "t01_golden_cross"
FAST, SLOW = 50, 200
ATR_MULT = 2.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=C.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars, db = C.load_daily(con)
    closes = [d["close"] for d in db]
    s_fast = sma(closes, FAST)
    s_slow = sma(closes, SLOW)
    atr14 = C.daily_atr(db, 14)
    start_i = C.window_start_idx(db)

    trades, skips = [], {}
    position = None  # dict when a long is open

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    for i in range(1, len(db)):
        if s_fast[i] is None or s_slow[i] is None or s_fast[i - 1] is None or s_slow[i - 1] is None:
            continue
        if db[i]["date"] < E.WINDOW_START_DATE:
            continue
        cross_up = s_fast[i - 1] <= s_slow[i - 1] and s_fast[i] > s_slow[i]
        cross_down = s_fast[i - 1] >= s_slow[i - 1] and s_fast[i] < s_slow[i]
        fill_idx = i + 1

        if position is None and cross_up:
            if fill_idx >= len(db):
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
                "signal_day": db[i]["date"],
            }
        elif position is not None and cross_down:
            if fill_idx >= len(db):
                exit_idx = len(db) - 1
                exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
                reason = "window_end_forced_close"
            else:
                exit_idx = fill_idx
                exit_ts, exit_price = C.first_minute_fill(bars, db, exit_idx)
                reason = "sma_cross_down"
            tr = C.make_trade(NAME, "long", db, bars,
                               position["entry_idx"], position["entry_ts"], position["entry_price"],
                               exit_idx, exit_ts, exit_price, reason, position["qty"],
                               position["risk_points"], slip=args.slippage,
                               notes=f"signal_day={position['signal_day']} exit_signal_day={db[i]['date']}")
            trades.append(tr)
            position = None

    if position is not None:
        exit_idx = len(db) - 1
        exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
        tr = C.make_trade(NAME, "long", db, bars,
                           position["entry_idx"], position["entry_ts"], position["entry_price"],
                           exit_idx, exit_ts, exit_price, "window_end_forced_close", position["qty"],
                           position["risk_points"], slip=args.slippage,
                           notes=f"signal_day={position['signal_day']} still open at window end")
        trades.append(tr)

    out, payload = E.save_results(NAME, trades, extra={
        "skips": skips, "risk_pct": args.risk, "slippage": args.slippage,
        "params": {"fast_sma": FAST, "slow_sma": SLOW, "sizing_atr_mult": ATR_MULT,
                    "warmup_start": C.WARMUP_START},
    })
    m = payload["metrics"]
    print(f"{NAME}: {len(trades)} trades (skips: {skips})")
    if m:
        print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
              f"PF {m['profit_factor']} | maxDD {m['max_drawdown_pct_of_peak']}% | "
              f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    else:
        print("  no trades -- sample too small to judge (expected for Golden Cross's ~1/2yr frequency)")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
