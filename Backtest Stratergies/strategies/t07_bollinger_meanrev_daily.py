"""#7 Bollinger Bands Mean Reversion, daily NIFTY, 2022-2023 (Top-10 #7,
Connors & Alvarez).

Executed via index points at NIFTY-futures cost economics, not real options
-- this archive's option chain only has priced data in each contract's last
~5-7 trading days before expiry, which cannot support a multi-week/month
hold; see engine.py's module docstring for the options convention used
everywhere else in this project. Cost/sizing machinery lives in
strategies/_swing_common.py.

RULE: daily 20-SMA +/- 2 std bands. Buy SIGNAL: a day's close at/below the
lower band. CONFIRMATION: the NEXT day's close moves back above the lower
band -> buy at the fill when that happens (mirrors s01's intraday version
of this same idea -- "wait for next candle to close back inside band ->
enter at that close" -- just on daily bars instead of 1-min bars). Long
only, one position at a time. SCALE OUT: exit at the mid-band (20-SMA) or
upper band, whichever the price reaches first (checked via daily close
against that day's band values), or after a MAX HOLD of 10 trading days if
neither is reached. The spec does not state a hold cap; 10 trading days is
this script's own choice (roughly 2 calendar weeks), picked as a reasonable
stop against an otherwise-indefinite hold for a mean-reversion setup.

INDICATOR WARM-UP: the 20-SMA/bands need only 20 bars, but index bars are
still loaded from 2020-01-01 (_swing_common.WARMUP_START) for consistency
with the rest of this batch and so the sizing ATR(14) below is never cold.

FILL (deviation from this batch's usual next-day-first-minute default, per
the spec's own wording -- documented explicitly here since the top-level
brief requires it): ENTRY fills at the CONFIRMATION day's own LAST
session-minute close (_swing_common.last_minute_fill), i.e. the very close
that confirms re-entry above the lower band -- not the following day's
open. This mirrors s01's "enter at that close" convention exactly, applied
to daily bars. EXIT (mid-band/upper-band touch, or the 10-day cap) also
fills at that exit day's own last session-minute close, for the same
reason: the exit condition is itself a daily-close test, so the realistic
fill is that same day's close. Because both entry and exit already happen
at day-end, held-to-dayend is always None for this script's trades, as
instructed for scripts whose exit already IS session end.

SIZING (judgment call -- the spec doesn't define a hard stop): risk
distance is entry_price - (confirmation day's LOW - 2 points), mirroring
s01's stop convention ("1-2 points beyond the low of the confirmation
candle"), applied to the daily confirmation bar instead of a 1-min one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
import strategies._swing_common as C  # noqa: E402

from options_bot.indicators import bollinger_bands  # noqa: E402

NAME = "t07_bollinger_meanrev_daily"
BB_PERIOD = 20
BB_STD = 2.0
MAX_HOLD_DAYS = 10
STOP_BUFFER = 2.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=C.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars, db = C.load_daily(con)
    closes = [d["close"] for d in db]
    mid, up, low = bollinger_bands(closes, BB_PERIOD, BB_STD)
    n = len(db)
    start_i = C.window_start_idx(db)

    trades, skips = [], {}

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    i = max(start_i, BB_PERIOD)
    while i < n - 1:
        if low[i] is None or db[i]["date"] < E.WINDOW_START_DATE:
            i += 1
            continue
        touched_lower = closes[i] <= low[i]
        if not touched_lower:
            i += 1
            continue
        confirm_idx = i + 1
        if low[confirm_idx] is None:
            i += 1
            continue
        confirmed = closes[confirm_idx] > low[confirm_idx]
        if not confirmed:
            i += 1
            continue

        entry_ts, entry_price = C.last_minute_fill(bars, db, confirm_idx)
        stop = db[confirm_idx]["low"] - STOP_BUFFER
        risk_points = entry_price - stop
        if risk_points <= 0:
            skip("nonpositive_risk")
            i += 1
            continue
        qty = C.size_lots(E.CAPITAL, args.risk, risk_points)
        if qty <= 0:
            skip("qty_zero")
            i += 1
            continue

        # walk forward for the scale-out
        exit_idx, exit_reason = None, None
        cap_idx = min(confirm_idx + MAX_HOLD_DAYS, n - 1)
        for j in range(confirm_idx + 1, cap_idx + 1):
            if mid[j] is not None and closes[j] >= mid[j]:
                exit_idx, exit_reason = j, "mid_band"
                break
            if up[j] is not None and closes[j] >= up[j]:
                exit_idx, exit_reason = j, "upper_band"
                break
        if exit_idx is None:
            exit_idx, exit_reason = cap_idx, f"max_hold_{MAX_HOLD_DAYS}d"
        exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)

        tr = C.make_trade(NAME, "long", db, bars,
                           confirm_idx, entry_ts, entry_price, exit_idx, exit_ts, exit_price,
                           exit_reason, qty, risk_points, slip=args.slippage,
                           notes=f"lower_band_touch_day={db[i]['date']} "
                                 f"confirmation_day={db[confirm_idx]['date']} "
                                 f"stop={round(stop, 2)}")
        trades.append(tr)
        i = exit_idx + 1

    out, payload = E.save_results(NAME, trades, extra={
        "skips": skips, "risk_pct": args.risk, "slippage": args.slippage,
        "params": {"bb_period": BB_PERIOD, "bb_std": BB_STD, "max_hold_days": MAX_HOLD_DAYS,
                    "stop_buffer_points": STOP_BUFFER, "warmup_start": C.WARMUP_START},
        "max_hold_note": f"Spec doesn't state a hold cap; {MAX_HOLD_DAYS} trading days is this "
                          f"script's own choice, documented in the module docstring.",
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
