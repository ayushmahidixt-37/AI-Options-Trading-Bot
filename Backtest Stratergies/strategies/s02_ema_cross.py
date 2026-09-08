"""#2 EMA 9/21 Crossover + RSI Momentum Filter -- 5-min timeframe, NIFTY, 2022-2023.

Long: EMA9 crosses above EMA21 on candle close + RSI(14)>50. Short: EMA9
crosses below EMA21 + RSI(14)<50. Stop: low (long) / high (short) of the
crossover candle +/- 1 tick (E.TICK). Target: none fixed -- trailing exit,
close when price closes back across EMA21 against the position.

Signal/stop/exit are computed on the index; the trade actually taken is a
real ATM option (CE for the long signal, PE for the short one) -- see
engine.py's module docstring.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

from options_bot.indicators import ema, rsi  # noqa: E402

NAME = "s02_ema_cross"
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14


def resample_5min(bars):
    """Aggregate session-filtered 1-min bars into 5-min buckets anchored 9:15.
    Copied from s03_orb.py's resample_5min per this project's convention."""
    out = []
    cur = None
    for ts, o, h, l, c in bars:
        bucket_min = ((ts.hour * 60 + ts.minute) - (9 * 60 + 15)) // 5
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


def signals(bars_5m, days_5m):
    closes = [b[4] for b in bars_5m]
    e9, e21, r14 = ema(closes, EMA_FAST), ema(closes, EMA_SLOW), rsi(closes, RSI_PERIOD)
    sigs = []
    for day, lo, hi in days_5m:
        i = max(lo, 1)
        while i < hi:
            if i < EMA_SLOW or r14[i] is None:
                i += 1
                continue
            _, o, h, l, c = bars_5m[i]
            longx = e9[i] > e21[i] and e9[i - 1] <= e21[i - 1] and r14[i] > 50
            shortx = e9[i] < e21[i] and e9[i - 1] >= e21[i - 1] and r14[i] < 50
            if longx:
                sig, j = E.walk_signal(
                    bars_5m, day, i, "long", c, l - E.TICK, hi,
                    exit_fn=lambda k: bars_5m[k][4] < e21[k])
                sigs.append(sig)
                i = j
            elif shortx:
                sig, j = E.walk_signal(
                    bars_5m, day, i, "short", c, h + E.TICK, hi,
                    exit_fn=lambda k: bars_5m[k][4] > e21[k])
                sigs.append(sig)
                i = j
            i += 1
    return sigs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars = E.filter_session(E.load_index_bars(con))
    bars_5m = resample_5min(bars)
    days_5m = E.day_index(bars_5m)
    all_expiries = E.list_expiries(con)

    sigs = signals(bars_5m, days_5m)
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

    out, payload = E.save_results(NAME, trades, extra={
        "signals_found": len(sigs), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage, "params": {
            "timeframe": "FIVE_MINUTE", "ema_fast": EMA_FAST, "ema_slow": EMA_SLOW,
            "rsi_period": RSI_PERIOD,
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
