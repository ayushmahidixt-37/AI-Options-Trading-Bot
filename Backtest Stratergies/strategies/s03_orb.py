"""#3 Opening Range Breakout -- 5-min timeframe per spec, NIFTY, 2022-2023.

Range = high/low of the first three 5-min candles (9:15-9:30). Skip the day
if range < 40 index points. Long on first 5-min close above range high,
short on first close below range low; stop = opposite end of range; target
= entry +/- 1.75x range size; max 2 breakout attempts/day.

Signal and stop/target are computed on the index; the trade actually taken
is a real ATM option (CE for the long breakout, PE for the short one) --
see engine.py's module docstring for why and how.
"""

from __future__ import annotations

import argparse
import sys
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s03_orb"
MIN_RANGE = 40.0
TARGET_MULT = 1.75
MAX_ATTEMPTS = 2


def _resample(bars, bucket_size_min):
    """Aggregate session-filtered 1-min bars into `bucket_size_min`-min buckets
    anchored 9:15."""
    out = []
    cur = None
    for ts, o, h, l, c in bars:
        bucket_min = ((ts.hour * 60 + ts.minute) - (9 * 60 + 15)) // bucket_size_min
        key = (ts.date(), bucket_min)
        if cur is None or cur[0] != key:
            if cur is not None:
                out.append(cur[1])
            cur = (key, [ts, o, h, l, c])
        else:
            b = cur[1]
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
    if cur is not None:
        out.append(cur[1])
    return [tuple(b) for b in out]


def resample_5min(bars):
    """Aggregate session-filtered 1-min bars into 5-min buckets anchored 9:15."""
    return _resample(bars, 5)


def resample_15min(bars):
    """Aggregate session-filtered 1-min bars into 15-min buckets anchored 9:15."""
    return _resample(bars, 15)


# Opening-range candle count per timeframe: 3 x 5-min = first 15 minutes
# (9:15-9:30, matches the module docstring); 2 x 15-min = first two 15-min
# candles (9:15-9:45), matching the 8-year NIFTY ORB spec this project cites.
RANGE_CANDLES = {"FIVE_MINUTE": 3, "FIFTEEN_MINUTE": 2}


def signals(bars_tf, days_tf, min_range=MIN_RANGE, target_mult=TARGET_MULT,
            range_candles=3):
    sigs = []
    for day, lo, hi in days_tf:
        opening = bars_tf[lo:hi]
        if len(opening) < range_candles:
            continue
        first_n = opening[:range_candles]
        rh = max(b[2] for b in first_n)
        rl = min(b[3] for b in first_n)
        size = rh - rl
        if size < min_range:
            continue
        attempts = 0
        i = lo + range_candles
        while i < hi and attempts < MAX_ATTEMPTS:
            _, o, h, l, c = bars_tf[i]
            if c > rh:
                sig, j = E.walk_signal(bars_tf, day, i, "long", c, rl, hi,
                                        target=c + target_mult * size)
                sigs.append(sig)
                attempts += 1
                i = j
            elif c < rl:
                sig, j = E.walk_signal(bars_tf, day, i, "short", c, rh, hi,
                                        target=c - target_mult * size)
                sigs.append(sig)
                attempts += 1
                i = j
            i += 1
    return sigs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    ap.add_argument("--timeframe", choices=["FIVE_MINUTE", "FIFTEEN_MINUTE"],
                     default="FIVE_MINUTE")
    ap.add_argument("--min-range", type=float, default=MIN_RANGE)
    ap.add_argument("--target-mult", type=float, default=TARGET_MULT)
    ap.add_argument("--label", default=None,
                     help="if set, save to results/sweeps/<NAME>_<label>.json "
                          "instead of results/<NAME>.json")
    args = ap.parse_args(argv)

    con = E.connect()
    bars = E.filter_session(E.load_index_bars(con))
    resample_fn = resample_5min if args.timeframe == "FIVE_MINUTE" else resample_15min
    bars_tf = resample_fn(bars)
    days_tf = E.day_index(bars_tf)
    all_expiries = E.list_expiries(con)

    range_candles = RANGE_CANDLES[args.timeframe]
    sigs = signals(bars_tf, days_tf, min_range=args.min_range,
                    target_mult=args.target_mult, range_candles=range_candles)
    trades, skips = [], {}
    for s in sigs:
        tr, why = E.build_directional_option_trade(
            con, NAME, s.day, s.side, s.entry_at, s.exit_at, s.reason,
            s.entry_index, s.stop_index, slip=args.slippage, risk_pct=args.risk,
            all_expiries=all_expiries)
        if tr is None:
            skips[why] = skips.get(why, 0) + 1
        else:
            trades.append(tr)

    save_name = f"sweeps/{NAME}_{args.label}" if args.label else NAME
    out, payload = E.save_results(save_name, trades, extra={
        "signals_found": len(sigs), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage, "params": {
            "timeframe": args.timeframe, "min_range": args.min_range,
            "target_mult": args.target_mult, "max_attempts": MAX_ATTEMPTS,
        },
    })
    m = payload["metrics"]
    print(f"{NAME}: {len(sigs)} signals -> {len(trades)} trades (skips: {skips})")
    if m:
        print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
              f"PF {m['profit_factor']} | maxDD {m['max_drawdown_pct_of_peak']}% | "
              f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
