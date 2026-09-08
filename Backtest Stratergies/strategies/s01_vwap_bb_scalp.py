"""#1 VWAP + Bollinger Band Mean-Reversion Scalp -- 1-min timeframe, NIFTY, 2022-2023.

Indicators: Bollinger Bands (20-SMA, +/-2 std) and RSI(14) from
options_bot.indicators; VWAP is session-anchored volume-weighted average
price, but the index series has no volume (see engine.py's module
docstring), so this substitutes session TWAP via E.twap_for -- the same
TWAP-PROXY convention already used elsewhere in this project. Results are
labelled accordingly; this is NOT true VWAP.

Long: candle closes below lower BB + RSI(14)<30 + close below TWAP -> wait
for the NEXT candle to close back inside the band -> enter at that close.
Short: mirror. Stop: 2 points beyond the low/high of the confirmation
candle (spec says 1-2, this uses 2). Target: TWAP per-bar (callable target
passed to E.walk_signal, matching the pattern the engine already supports).

Signal/stop/target are computed on the index; the trade actually taken is a
real ATM option (CE for the long signal, PE for the short one) -- see
engine.py's module docstring.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

from options_bot.indicators import bollinger_bands, rsi  # noqa: E402

NAME = "s01_vwap_bb_scalp"
BB_PERIOD = 20
BB_STD = 2.0
RSI_PERIOD = 14
STOP_BUFFER = 2.0


def signals(bars, days):
    closes = [b[4] for b in bars]
    mid, up, low = bollinger_bands(closes, BB_PERIOD, BB_STD)
    r14 = rsi(closes, RSI_PERIOD)
    sigs = []
    for day, lo, hi in days:
        tw = E.twap_for(bars, lo, hi)
        i = lo
        while i < hi - 1:
            k = i - lo
            if low[i] is None or up[i] is None or r14[i] is None:
                i += 1
                continue
            _, o, h, l, c = bars[i]
            side = None
            if c < low[i] and r14[i] < 30 and c < tw[k]:
                side = "long"
            elif c > up[i] and r14[i] > 70 and c > tw[k]:
                side = "short"
            if side is not None:
                n = i + 1  # strictly the next candle
                if low[n] is not None and up[n] is not None:
                    _, no, nh, nl, nc = bars[n]
                    inside = (nc > low[n]) if side == "long" else (nc < up[n])
                    if inside:
                        stop = (nl - STOP_BUFFER) if side == "long" else (nh + STOP_BUFFER)
                        valid = (nc > stop) if side == "long" else (nc < stop)
                        if valid:
                            sig, j = E.walk_signal(
                                bars, day, n, side, nc, stop, hi,
                                target=lambda q, lo_=lo: tw[q - lo_])
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
        "slippage": args.slippage,
        "vwap_substitution": "TWAP-PROXY: index has no volume, session TWAP used instead of VWAP",
        "params": {
            "timeframe": "ONE_MINUTE", "bb_period": BB_PERIOD, "bb_std": BB_STD,
            "rsi_period": RSI_PERIOD, "stop_buffer_pts": STOP_BUFFER,
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
