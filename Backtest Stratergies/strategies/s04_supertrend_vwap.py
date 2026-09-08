"""#4 Supertrend(7,2) + VWAP Trend-Following -- 5-min timeframe, NIFTY.

Supertrend via E.supertrend (ATR period, mult -- (7,2) is the improved
config adopted after the Phase-2 sweep beat the original (10,3) on every
axis out-of-sample; both remain CLI-overridable). VWAP substituted by
session TWAP via E.twap_for -- the index has no volume, same TWAP-PROXY
convention used elsewhere in this project; results labelled accordingly.

Long: Supertrend flips bullish on candle close + price above TWAP. Short:
flips bearish + price below TWAP. Stop: the Supertrend line value at entry,
trailing as it updates each candle (trail_fn). Target: none, hold until
Supertrend flips against the position (exit_fn). Cap: max trades/day
(default 3, CLI-overridable). Reference implementation for this exact
index-points logic: clean_room/backtest_index_scalps.py's s4_supertrend,
translated here to real options via E.build_directional_option_trade.

PHASE-4 ADDITIONS (see PHASE4_REPORT.md), all opt-in via CLI, default
behaviour unchanged:
  --timeframe-minutes   bucket size for the resample (was hardcoded 5)
  --max-trades-per-day  was a hardcoded module constant
  --regime              all (default) / gap_down / gap_up -- restrict
                         entries to days whose session open gapped beyond
                         the PRIOR day's high/low (clean_room/
                         gap_down_system.py's same classification, ported
                         here). gap_up is included as a negative control,
                         not because it's expected to work.
  --daily-trend-filter  only take a signal if the PRIOR day's close sits on
                         the same side of the prior day's own daily EMA(20)
                         as the signal direction (no lookahead: the signal
                         day itself is never used to compute its own
                         filter). Off by default.
  --start / --end       explicit ISO window override, passed straight
                         through to E.load_index_bars/E.list_expiries
                         instead of relying on engine.py's module-level
                         WINDOW_START/END. Use this for full-history runs --
                         NOT engine.py mutation, which raced against a
                         background loop earlier in this project and
                         silently mixed two different windows in one sweep.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
from options_bot.indicators import ema  # noqa: E402

NAME = "s04_supertrend_vwap"
ST_PERIOD = 7
ST_MULT = 2.0
MAX_TRADES_PER_DAY = 3
DAILY_TREND_EMA = 20


def resample(bars, minutes):
    """Aggregate session-filtered 1-min bars into `minutes`-min buckets
    anchored 9:15. Generalizes the old hardcoded resample_5min (kept below
    as a thin wrapper for anything that still imports it by name)."""
    out = []
    cur = None
    for ts, o, h, l, c in bars:
        bucket_min = ((ts.hour * 60 + ts.minute) - (9 * 60 + 15)) // minutes
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
    return resample(bars, 5)


def daily_bars(bars_1m, days_1m):
    """One O/H/L/C per session, built from the same session-filtered 1-min
    bars everything else here already loads -- no extra DB query."""
    out = []
    for day, lo, hi in days_1m:
        seg = bars_1m[lo:hi]
        out.append((day, seg[0][1], max(b[2] for b in seg), min(b[3] for b in seg), seg[-1][4]))
    return out


def classify_gaps(daily):
    """day -> 'gap_down' / 'gap_up' / 'inside', vs the PRIOR session's
    high/low. Same definition as clean_room/gap_down_system.py's classify()."""
    out = {}
    for i in range(1, len(daily)):
        day, o, h, l, c = daily[i]
        _, po, ph, pl, pc = daily[i - 1]
        if o < pl:
            out[day] = "gap_down"
        elif o > ph:
            out[day] = "gap_up"
        else:
            out[day] = "inside"
    return out


def daily_trend_sides(daily):
    """day -> 'long_only' / 'short_only' / 'any' (warm-up), from the PRIOR
    day's close vs the PRIOR day's own EMA(20) of daily closes -- never the
    signal day's own not-yet-complete session, so this can't look ahead
    into the trade it's filtering."""
    closes = [d[4] for d in daily]
    e20 = ema(closes, DAILY_TREND_EMA)
    out = {}
    for i in range(1, len(daily)):
        day = daily[i][0]
        if i - 1 < DAILY_TREND_EMA:
            out[day] = "any"  # EMA not warmed up yet -- don't filter blind
        else:
            out[day] = "long_only" if closes[i - 1] > e20[i - 1] else "short_only"
    return out


def signals(bars_tf, days_tf, period=ST_PERIOD, mult=ST_MULT,
            max_trades_per_day=MAX_TRADES_PER_DAY, day_filter=None):
    """day_filter(day) -> "any" / "long_only" / "short_only" / "none".
    "none" skips the whole day (used by --regime); "long_only"/"short_only"
    still scan the day but only take signals matching that side (used by
    --daily-trend-filter, which allows only WITH-trend entries, not a
    blanket day skip)."""
    line, direction = E.supertrend(bars_tf, period, mult)
    sigs = []
    for day, lo, hi in days_tf:
        allowed = day_filter(day) if day_filter is not None else "any"
        if allowed == "none":
            continue
        tw = E.twap_for(bars_tf, lo, hi)
        i, taken = max(lo, 1), 0
        while i < hi and taken < max_trades_per_day:
            if line[i] is None or line[i - 1] is None:
                i += 1
                continue
            _, o, h, l, c = bars_tf[i]
            k = i - lo
            if (allowed in ("any", "long_only")
                    and direction[i] == 1 and direction[i - 1] == -1 and c > tw[k]):
                sig, j = E.walk_signal(
                    bars_tf, day, i, "long", c, line[i], hi,
                    trail_fn=lambda q: line[q] if direction[q] == 1 else None,
                    exit_fn=lambda q: direction[q] == -1)
                sigs.append(sig)
                taken += 1
                i = j
            elif (allowed in ("any", "short_only")
                    and direction[i] == -1 and direction[i - 1] == 1 and c < tw[k]):
                sig, j = E.walk_signal(
                    bars_tf, day, i, "short", c, line[i], hi,
                    trail_fn=lambda q: line[q] if direction[q] == -1 else None,
                    exit_fn=lambda q: direction[q] == 1)
                sigs.append(sig)
                taken += 1
                i = j
            i += 1
    return sigs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    ap.add_argument("--period", type=int, default=ST_PERIOD)
    ap.add_argument("--mult", type=float, default=ST_MULT)
    ap.add_argument("--timeframe-minutes", type=int, default=5)
    ap.add_argument("--max-trades-per-day", type=int, default=MAX_TRADES_PER_DAY)
    ap.add_argument("--regime", choices=["all", "gap_down", "gap_up"], default="all")
    ap.add_argument("--daily-trend-filter", action="store_true",
                     help="only take a signal with-trend vs the prior day's EMA(20)")
    ap.add_argument("--start", default=E.WINDOW_START,
                     help="ISO datetime, e.g. 2020-08-03T00:00:00+05:30")
    ap.add_argument("--end", default=E.WINDOW_END)
    ap.add_argument("--label", default=None,
                     help="if set, save to results/sweeps/<NAME>_<label>.json "
                          "instead of results/<NAME>.json")
    args = ap.parse_args(argv)

    con = E.connect()
    bars = E.filter_session(E.load_index_bars(con, start=args.start, end=args.end))
    bars_tf = resample(bars, args.timeframe_minutes)
    days_tf = E.day_index(bars_tf)
    start_date = E.date.fromisoformat(args.start[:10])
    end_date = E.date.fromisoformat(args.end[:10])
    all_expiries = E.list_expiries(con, start=start_date, end=end_date)

    day_filter = None
    filter_desc = "none"
    if args.regime != "all":
        gaps = classify_gaps(daily_bars(bars, E.day_index(bars)))
        wanted = args.regime
        day_filter = lambda d: "any" if gaps.get(d) == wanted else "none"
        filter_desc = f"regime={args.regime}"
    if args.daily_trend_filter:
        sides = daily_trend_sides(daily_bars(bars, E.day_index(bars)))
        base_filter = day_filter
        if base_filter is None:
            day_filter = lambda d: sides.get(d, "any")
        else:
            def combined(d, base_filter=base_filter, sides=sides):
                if base_filter(d) == "none":
                    return "none"
                return sides.get(d, "any")
            day_filter = combined
        filter_desc += "+daily_trend_ema20" if filter_desc != "none" else "daily_trend_ema20"

    sigs = signals(bars_tf, days_tf, period=args.period, mult=args.mult,
                   max_trades_per_day=args.max_trades_per_day, day_filter=day_filter)
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
        "slippage": args.slippage, "window": f"{args.start}..{args.end}",
        "vwap_substitution": "TWAP-PROXY: index has no volume, session TWAP used instead of VWAP",
        "params": {
            "timeframe_minutes": args.timeframe_minutes, "supertrend_period": args.period,
            "supertrend_mult": args.mult, "max_trades_per_day": args.max_trades_per_day,
            "filter": filter_desc,
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
