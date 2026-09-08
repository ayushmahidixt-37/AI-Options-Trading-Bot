"""#2 RSI(2) Mean Reversion, daily NIFTY, 2022-2023 (Top-10 #2, Larry Connors).

Executed via index points at NIFTY-futures cost economics, not real options
-- this archive's option chain only has priced data in each contract's last
~5-7 trading days before expiry, which cannot support a multi-week/month
hold; see engine.py's module docstring for the options convention used
everywhere else in this project. Cost/sizing machinery lives in
strategies/_swing_common.py.

RULE: daily EMA(200) trend filter (price above it to be eligible long); buy
when daily RSI(2) < 10; sell/exit when RSI(2) > 70 OR the day's close moves
back above the PRIOR day's high, whichever condition is met first. Long
only (the EMA200 filter only ever arms the long side, per spec). One
position at a time.

INDICATOR WARM-UP: EMA(200) and RSI(2) are computed over index bars loaded
from 2020-01-01 (_swing_common.WARMUP_START) so the 200-EMA has ~2 years to
converge before 2022-01-01; no trade is entered on a signal dated before
WINDOW_START_DATE.

FILL: both entry and exit are signals confirmed by a day's daily close, so
both fill at the FIRST session-minute's close of the NEXT trading day
(_swing_common.first_minute_fill) -- a real, tradable 1-minute price.

SIZING (judgment call -- RSI(2) systems don't specify a hard stop): risk
distance for the capital-risk-% formula is 2x daily ATR(14) at the entry
SIGNAL day (the day RSI(2) closed below 10), same convention as t01/t03/t05/
t06 for consistency across the batch. Sizing-only -- the strategy still only
exits on RSI(2)>70 or the prior-day-high break, never on this ATR distance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
import strategies._swing_common as C  # noqa: E402

from options_bot.indicators import ema, rsi  # noqa: E402

NAME = "t02_rsi2_meanrev_daily"
EMA_PERIOD = 200
RSI_PERIOD = 2
RSI_BUY_BELOW = 10.0
RSI_SELL_ABOVE = 70.0
ATR_MULT = 2.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=C.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars, db = C.load_daily(con)
    closes = [d["close"] for d in db]
    e200 = ema(closes, EMA_PERIOD)
    r2 = rsi(closes, RSI_PERIOD)
    atr14 = C.daily_atr(db, 14)
    start_i = C.window_start_idx(db)

    trades, skips = [], {}

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    i = max(start_i, EMA_PERIOD, RSI_PERIOD + 1)
    n = len(db)
    while i < n:
        if r2[i] is None or db[i]["date"] < E.WINDOW_START_DATE:
            i += 1
            continue
        uptrend = closes[i] > e200[i]
        if uptrend and r2[i] < RSI_BUY_BELOW:
            entry_fill_idx = i + 1
            if entry_fill_idx >= n:
                skip("no_next_day_for_entry")
                i += 1
                continue
            if atr14[i] is None:
                skip("no_atr")
                i += 1
                continue
            risk_points = ATR_MULT * atr14[i]
            qty = C.size_lots(E.CAPITAL, args.risk, risk_points)
            if qty <= 0:
                skip("qty_zero")
                i += 1
                continue
            entry_ts, entry_price = C.first_minute_fill(bars, db, entry_fill_idx)
            entry_signal_day = db[i]["date"]

            # walk forward looking for the exit signal
            j = entry_fill_idx
            exit_idx = exit_ts = exit_price = exit_reason = None
            while j < n:
                prior_high = db[j - 1]["high"] if j - 1 >= 0 else None
                rsi_exit = r2[j] is not None and r2[j] > RSI_SELL_ABOVE
                high_break = prior_high is not None and db[j]["close"] > prior_high
                if rsi_exit or high_break:
                    exit_fill_idx = j + 1
                    reason = "rsi2_above_70" if rsi_exit else "close_above_prior_high"
                    if exit_fill_idx >= n:
                        exit_idx = n - 1
                        exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
                        exit_reason = "window_end_forced_close"
                    else:
                        exit_idx = exit_fill_idx
                        exit_ts, exit_price = C.first_minute_fill(bars, db, exit_idx)
                        exit_reason = reason
                    break
                j += 1
            if exit_idx is None:
                # never triggered within the loaded window -- force-close at last bar
                exit_idx = n - 1
                exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
                exit_reason = "window_end_forced_close"

            tr = C.make_trade(NAME, "long", db, bars,
                               entry_fill_idx, entry_ts, entry_price,
                               exit_idx, exit_ts, exit_price, exit_reason, qty, risk_points,
                               slip=args.slippage,
                               notes=f"entry_signal_day={entry_signal_day}")
            trades.append(tr)
            i = exit_idx + 1
            continue
        i += 1

    out, payload = E.save_results(NAME, trades, extra={
        "skips": skips, "risk_pct": args.risk, "slippage": args.slippage,
        "params": {"ema_period": EMA_PERIOD, "rsi_period": RSI_PERIOD,
                    "rsi_buy_below": RSI_BUY_BELOW, "rsi_sell_above": RSI_SELL_ABOVE,
                    "sizing_atr_mult": ATR_MULT, "warmup_start": C.WARMUP_START},
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
