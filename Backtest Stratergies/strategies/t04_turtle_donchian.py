"""#4 Turtle Trading, Donchian Channel Breakout (System 1), daily NIFTY,
2022-2023 (Top-10 #4).

Executed via index points at NIFTY-futures cost economics, not real options
-- this archive's option chain only has priced data in each contract's last
~5-7 trading days before expiry, which cannot support a multi-week/month
hold; see engine.py's module docstring for the options convention used
everywhere else in this project. Cost/sizing machinery lives in
strategies/_swing_common.py.

RULE (System 1, literal, unoptimized -- the file's own caveat calls this
"the single most parameter-fragile strategy on this list"; it is NOT tuned
here, just implemented and reported): entry on a 20-trading-day high/low
breakout of daily CLOSE (long on close > preceding 20-day high, short on
close < preceding 20-day low -- "preceding" excludes the signal day itself);
exit on a 10-trading-day OPPOSITE-direction breakout of daily close (a long
exits when close < preceding 10-day low; a short exits when close >
preceding 10-day high). One position at a time -- pyramiding "units" from
the original Turtle rules are NOT implemented (the batch's own capital-
risk-% sizing formula replaces Turtle's ATR/"N" sizing entirely, per the
brief, and add-on units don't have a defined role once that formula owns
sizing).

INDICATOR WARM-UP: index bars loaded from 2020-01-01
(_swing_common.WARMUP_START) so the 20-day channel is never cold at
2022-01-01. No signal before WINDOW_START_DATE is traded.

FILL: entries/exits are confirmed by a day's daily close breaking a
channel; both fill at the FIRST session-minute's close of the NEXT trading
day (_swing_common.first_minute_fill).

SIZING: stop = the 10-day channel's OPPOSITE level AT ENTRY (given
explicitly by the brief) -- e.g. for a long, the 10-day low computed as of
the entry signal day. risk_points = |entry_price - that level|.

If a position is still open when the loaded window ends, it is
force-closed at the last available day's own last session-minute close,
reason "window_end_forced_close".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
import strategies._swing_common as C  # noqa: E402

NAME = "t04_turtle_donchian"
ENTRY_LOOKBACK = 20
EXIT_LOOKBACK = 10


def rolling_extreme(values, period, fn):
    """values[i] -> fn(values[i-period:i]) (PRECEDING window, excludes i).
    None until `period` bars of history exist before i."""
    out = [None] * len(values)
    for i in range(len(values)):
        if i < period:
            continue
        window = values[i - period:i]
        out[i] = fn(window)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=C.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    bars, db = C.load_daily(con)
    closes = [d["close"] for d in db]
    n = len(db)

    hi20 = rolling_extreme(closes, ENTRY_LOOKBACK, max)
    lo20 = rolling_extreme(closes, ENTRY_LOOKBACK, min)
    hi10 = rolling_extreme(closes, EXIT_LOOKBACK, max)
    lo10 = rolling_extreme(closes, EXIT_LOOKBACK, min)

    trades, skips = [], {}

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    i = max(ENTRY_LOOKBACK, C.window_start_idx(db))
    position = None  # dict with side/entry info when open
    while i < n:
        if db[i]["date"] < E.WINDOW_START_DATE:
            i += 1
            continue

        if position is None:
            side = None
            if hi20[i] is not None and closes[i] > hi20[i]:
                side = "long"
            elif lo20[i] is not None and closes[i] < lo20[i]:
                side = "short"
            if side is not None:
                fill_idx = i + 1
                if fill_idx >= n:
                    skip("no_next_day_for_entry")
                    i += 1
                    continue
                opp_level = lo10[i] if side == "long" else hi10[i]
                if opp_level is None:
                    skip("no_10d_channel")
                    i += 1
                    continue
                risk_points = abs(closes[i] - opp_level)
                qty = C.size_lots(E.CAPITAL, args.risk, risk_points)
                if qty <= 0:
                    skip("qty_zero")
                    i += 1
                    continue
                entry_ts, entry_price = C.first_minute_fill(bars, db, fill_idx)
                position = {
                    "side": side, "entry_idx": fill_idx, "entry_ts": entry_ts,
                    "entry_price": entry_price, "qty": qty, "risk_points": risk_points,
                    "signal_day": db[i]["date"], "opp_level_at_entry": opp_level,
                }
                i += 1
                continue
        else:
            side = position["side"]
            exit_signal = ((side == "long" and lo10[i] is not None and closes[i] < lo10[i]) or
                            (side == "short" and hi10[i] is not None and closes[i] > hi10[i]))
            if exit_signal:
                fill_idx = i + 1
                if fill_idx >= n:
                    exit_idx = n - 1
                    exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
                    reason = "window_end_forced_close"
                else:
                    exit_idx = fill_idx
                    exit_ts, exit_price = C.first_minute_fill(bars, db, exit_idx)
                    reason = "10day_opposite_breakout"
                tr = C.make_trade(NAME, side, db, bars,
                                   position["entry_idx"], position["entry_ts"], position["entry_price"],
                                   exit_idx, exit_ts, exit_price, reason, position["qty"],
                                   position["risk_points"], slip=args.slippage,
                                   notes=f"entry_signal_day={position['signal_day']} "
                                         f"opp_10d_level_at_entry={round(position['opp_level_at_entry'], 2)} "
                                         f"exit_signal_day={db[i]['date']}")
                trades.append(tr)
                position = None
        i += 1

    if position is not None:
        exit_idx = n - 1
        exit_ts, exit_price = C.last_minute_fill(bars, db, exit_idx)
        tr = C.make_trade(NAME, position["side"], db, bars,
                           position["entry_idx"], position["entry_ts"], position["entry_price"],
                           exit_idx, exit_ts, exit_price, "window_end_forced_close", position["qty"],
                           position["risk_points"], slip=args.slippage,
                           notes=f"entry_signal_day={position['signal_day']} still open at window end")
        trades.append(tr)

    out, payload = E.save_results(NAME, trades, extra={
        "skips": skips, "risk_pct": args.risk, "slippage": args.slippage,
        "caveat": "Most parameter-fragile strategy in this batch per the source file -- "
                  "not tuned, System 1 implemented literally.",
        "params": {"entry_lookback": ENTRY_LOOKBACK, "exit_lookback": EXIT_LOOKBACK,
                    "warmup_start": C.WARMUP_START,
                    "sizing": "risk = |entry - opposite 10-day channel level at entry| (given by brief)"},
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
