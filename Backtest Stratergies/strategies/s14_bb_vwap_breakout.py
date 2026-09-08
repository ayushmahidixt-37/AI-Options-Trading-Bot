"""#13-14 (second variant) Bollinger + VWAP Breakout -- 15-min signal / 5-min
entry, NIFTY, 2022-2023.

JUDGMENT CALL / BEST-EFFORT TRANSLATION: this strategy is flagged in
strategies-and-parameters.md as "reference-only, lower priority", with the
only description being the one line "Bollinger+VWAP breakout (15-min
signal / 5-min entry, volume > average)" and a pointer to a
strategy-reference-catalog.md file that does not exist anywhere in this
repo. There is no fuller spec to find. What follows is this file's own
literal reading of that one line, not a transcription of a fuller rule set
that doesn't exist -- treat every rule below as a reconstruction, not a
verified spec:

  - 15-min Bollinger Bands (20-period, 2 std) on a 15-min resample of
    session bars (resample_15min below, same pattern as s03_orb.py's
    resample_5min).
  - Signal = a 15-min candle CLOSES outside the band for the first time
    (previous 15-min close was still inside) -- above the upper band for a
    long breakout, below the lower band for a short breakout. This is a
    breakout continuation rule, the mirror-opposite of s01's mean-reversion
    rule (which enters on the candle that closes back INSIDE the band).
  - Entry confirmation: the next 5-min candle (the one immediately
    following the 15-min signal candle's close) must itself close further
    in the breakout direction than the 15-min signal close -- i.e. price
    keeps moving the same way on the finer timeframe. Entry is at that
    5-min candle's close.
  - Stop: the 15-min band's OPPOSITE side at signal time (lower band for a
    long, upper band for a short).
  - Target: none fixed. Trail-exit when price closes back across the
    15-min midline (SMA20), tracked on the 5-min entry timeframe against
    the most recently *completed* 15-min midline value as of each 5-min
    bar (mid15_track below).

"volume > average" from the one-line spec is NOT applied, for the same
reason s06 is not run at all: the NIFTY index series has volume IS NULL on
100% of rows in this archive, and there is no honest substitute for a
volume filter (unlike VWAP, which this project substitutes with TWAP when
labelled -- see engine.py's docstring). This omission is flagged again in
`extra` below. VWAP itself is not separately computed here beyond the
Bollinger midline; the one-line spec names "Bollinger+VWAP" but gives no
distinct rule for how VWAP participates beyond the bands/midline already
described, so no further TWAP-proxy indicator is added on top of the BB
midline -- adding one would be inventing a rule the spec doesn't state.

Signal/stop/exit are computed on the index; the trade actually taken is a
real ATM option (CE for the long breakout, PE for the short one) -- see
engine.py's module docstring.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

from options_bot.indicators import bollinger_bands  # noqa: E402

NAME = "s14_bb_vwap_breakout"
BB_PERIOD = 20
BB_STD = 2.0


def resample_5min(bars):
    """Copied from s03_orb.py's resample_5min per this project's convention."""
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


def resample_15min(bars):
    """Same pattern as resample_5min, 15-min buckets anchored 9:15."""
    out = []
    cur = None
    for ts, o, h, l, c in bars:
        bucket_min = ((ts.hour * 60 + ts.minute) - (9 * 60 + 15)) // 15
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


def mid15_track(bars_15m, mid15, lo15, hi15, bars_5m, lo5, hi5):
    """For each 5-min bar in [lo5,hi5), the most recently *completed* 15-min
    bar's midline value as of that 5-min bar's start (None until the first
    15-min bar has fully closed)."""
    out = []
    j = lo15 - 1  # index of the last 15-min bar known to be closed
    for k5 in range(lo5, hi5):
        ts5 = bars_5m[k5][0]
        while j + 1 < hi15 and bars_15m[j + 1][0] + timedelta(minutes=15) <= ts5:
            j += 1
        out.append(mid15[j] if j >= lo15 else None)
    return out


def find_5m_at_or_after(bars_5m, lo5, hi5, ts):
    for k in range(lo5, hi5):
        if bars_5m[k][0] >= ts:
            return k
    return None


def signals(bars_5m, days_5m, bars_15m, days_15m):
    closes15 = [b[4] for b in bars_15m]
    mid15, up15, low15 = bollinger_bands(closes15, BB_PERIOD, BB_STD)

    days5_by_date = {d: (lo, hi) for d, lo, hi in days_5m}
    sigs = []
    for day, lo15, hi15 in days_15m:
        if day not in days5_by_date:
            continue
        lo5, hi5 = days5_by_date[day]
        mid_by_5m = mid15_track(bars_15m, mid15, lo15, hi15, bars_5m, lo5, hi5)

        for i in range(lo15 + 1, hi15):
            if up15[i] is None or low15[i] is None or up15[i - 1] is None or low15[i - 1] is None:
                continue
            c15 = bars_15m[i][4]
            c15_prev = bars_15m[i - 1][4]
            long_break = c15 > up15[i] and c15_prev <= up15[i - 1]
            short_break = c15 < low15[i] and c15_prev >= low15[i - 1]
            if not (long_break or short_break):
                continue
            side = "long" if long_break else "short"
            stop = low15[i] if side == "long" else up15[i]

            sig_end_ts = bars_15m[i][0] + timedelta(minutes=15)
            n = find_5m_at_or_after(bars_5m, lo5, hi5, sig_end_ts)
            if n is None:
                continue
            _, no, nh, nl, nc = bars_5m[n]
            continuing = (nc > c15) if side == "long" else (nc < c15)
            if not continuing:
                continue
            valid = (nc > stop) if side == "long" else (nc < stop)
            if not valid:
                continue

            def make_exit(side_=side, off=lo5):
                def exit_fn(k):
                    m = mid_by_5m[k - off]
                    if m is None:
                        return False
                    return bars_5m[k][4] < m if side_ == "long" else bars_5m[k][4] > m
                return exit_fn

            sig, j = E.walk_signal(
                bars_5m, day, n, side, nc, stop, hi5, exit_fn=make_exit())
            sigs.append(sig)
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
    bars_15m = resample_15min(bars)
    days_15m = E.day_index(bars_15m)
    all_expiries = E.list_expiries(con)

    sigs = signals(bars_5m, days_5m, bars_15m, days_15m)
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
        "spec_status": "reference-only/lower-priority in strategies-and-parameters.md; "
                        "strategy-reference-catalog.md it points to does not exist in this repo -- "
                        "rules below are this file's own best-effort literal reconstruction from the "
                        "single spec line, not a transcription of a fuller verified spec",
        "volume_filter": "NOT APPLIED - no volume data in archive (index series, volume IS NULL on all rows), "
                          "same gap as s06; 'volume > average' from the one-line spec is omitted",
        "params": {
            "signal_timeframe": "FIFTEEN_MINUTE (resampled)", "entry_timeframe": "FIVE_MINUTE (resampled)",
            "bb_period": BB_PERIOD, "bb_std": BB_STD,
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
