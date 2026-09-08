"""#5 RSI(2) Pullback-in-Trend Scalp -- 1-min timeframe, NIFTY, 2022-2023.

EMA(200) trend filter, RSI(2). Long: price above EMA200 + RSI(2) drops below
10, then crosses back above 10 -> enter at that close, stop = low of the
pullback swing. Short: mirror (below EMA200, RSI(2) above 90 then back below
90, stop = high of the swing). Target: RSI(2) crosses back above 70
(long)/below 30 (short) OR fixed 2:1 reward:risk, whichever comes first --
E.walk_signal checks both `target=` and `exit_fn=` every bar and returns on
whichever triggers first, which is exactly "whichever first".

Reference implementation for this exact index-points swing-tracking logic:
clean_room/backtest_index_scalps.py's s5_rsi2_pullback, translated here to
real options via E.build_directional_option_trade.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

from options_bot.indicators import ema, rsi  # noqa: E402

NAME = "s05_rsi2_pullback"
EMA_TREND = 200
RSI_PERIOD = 2
REWARD_RISK_MULT = 2.0


def signals(bars, days):
    closes = [b[4] for b in bars]
    e200, r2 = ema(closes, EMA_TREND), rsi(closes, RSI_PERIOD)
    sigs = []
    for day, lo, hi in days:
        i = lo
        armed_l = armed_s = False
        swing_lo, swing_hi = float("inf"), float("-inf")
        while i < hi:
            if i < EMA_TREND or r2[i] is None:
                i += 1
                continue
            _, o, h, l, c = bars[i]
            if c > e200[i]:
                armed_s = False
                if r2[i] < 10:
                    armed_l = True
                    swing_lo = min(swing_lo, l)
                elif armed_l and r2[i] > 10:
                    stop = min(swing_lo, l)
                    if c > stop:
                        sig, j = E.walk_signal(
                            bars, day, i, "long", c, stop, hi,
                            target=c + REWARD_RISK_MULT * (c - stop),
                            exit_fn=lambda k: r2[k] is not None and r2[k] > 70)
                        sigs.append(sig)
                        i = j
                    armed_l, swing_lo = False, float("inf")
            else:
                armed_l = False
                if r2[i] > 90:
                    armed_s = True
                    swing_hi = max(swing_hi, h)
                elif armed_s and r2[i] < 90:
                    stop = max(swing_hi, h)
                    if stop > c:
                        sig, j = E.walk_signal(
                            bars, day, i, "short", c, stop, hi,
                            target=c - REWARD_RISK_MULT * (stop - c),
                            exit_fn=lambda k: r2[k] is not None and r2[k] < 30)
                        sigs.append(sig)
                        i = j
                    armed_s, swing_hi = False, float("-inf")
            i += 1
    return sigs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars = E.filter_session(E.load_index_bars(con))
    days = E.day_index(bars)
    all_expiries = E.list_expiries(con)

    sigs = signals(bars, days)
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
            "timeframe": "ONE_MINUTE", "ema_trend": EMA_TREND, "rsi_period": RSI_PERIOD,
            "reward_risk_mult": REWARD_RISK_MULT,
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
