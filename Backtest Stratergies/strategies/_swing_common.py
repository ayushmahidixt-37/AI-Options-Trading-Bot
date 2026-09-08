"""Shared helpers for t01-t07 -- the seven swing/position strategies in this
batch (Golden Cross, RSI(2) mean reversion, time-series momentum, Turtle
Donchian, day-of-week diagnostic, Turnaround Tuesday, Bollinger mean
reversion daily). NOT imported by any sXX_*.py script and does not modify
engine.py -- it only adds the futures-equivalent cost/sizing/fill machinery
those seven need on top of what engine.py already provides.

WHY THESE SEVEN DON'T TRADE REAL OPTIONS: they are multi-day/week/month
swing or position strategies. This archive's option instruments only have
PRICED market_candles rows in roughly the last 5-7 trading days before their
own expiry (verified directly: the 2022-03-31 NIFTY monthly expiry's ATM
call has rows only from 2022-03-25 onward -- 5 distinct days, nothing
earlier in March even though the contract existed all month). A swing
signal that fires weeks before expiry therefore has no real option price to
execute at. So every t0X script here executes on NIFTY index points at
NIFTY-futures cost economics instead of real options -- a deliberate,
data-driven exception, not a shortcut. See engine.py's module docstring for
the real-options convention used everywhere else in this project.

COST SCHEDULE (index points, per round trip) -- reused verbatim from
clean_room/backtest_index_scalps.py's module-level constants, NOT the
options-leg cost formula in engine.py (that one's STT/exchange/stamp rates
are option-premium rates; this is the separate, much lower futures-notional
schedule):
    notional = price * lot
    txn      = EXCHANGE_TXN_RATE * notional * 2
    charges  = BROKERAGE_ROUND_TRIP + STT_SELL_RATE*notional + txn
               + STAMP_DUTY_RATE*notional + GST_RATE*(BROKERAGE_ROUND_TRIP+txn)
    cost_points = charges / lot + 2 * slippage
Net rupees = (gross_points - cost_points) * lot_size * qty_lots.

INDICATOR WARM-UP (judgment call, applies to every t0X script): the report
window is 2022-01-01..2023-12-31, but a 200-day SMA/EMA or a 6-month
trailing-return lookback needs a long run-up before the window even starts.
Rather than let every strategy's first ~10-14 months of the window be
crippled by a cold indicator, each script loads index bars starting
WARMUP_START (2020-01-01, ~2 years of extra history -- the archive's NIFTY
index data goes back to 2017-04-03, confirmed directly, so this is real
priced history, not synthetic padding) and computes indicators over that
full extended series. Trades are still only ever entered/reported with a
signal day >= WINDOW_START_DATE (2022-01-01) -- the extra history warms up
the indicator, it never itself produces a trade.

FILL CONVENTION (default; some scripts override per their own spec -- see
each script's own docstring for its exact fill, as the top-level brief
requires): a signal is confirmed by a trading day's daily close (built from
the session-filtered 1-minute series: open=first minute, high=day max,
low=day min, close=last minute). Because that close is only known after the
session ends, the position is filled at the FIRST session-minute's CLOSE of
the NEXT trading day (`first_minute_fill`) -- a real, tradable 1-minute
price, not the previous day's already-closed print. `last_minute_fill`
(equal to that day's own daily close by construction, but sourced from the
1-minute series) is used for the handful of scripts whose own spec fills
"at the close" of the signal day itself (no next-day lag), and for the
held-to-dayend counterfactual on every early exit.
"""

from __future__ import annotations

import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

from options_bot.indicators import atr_series  # noqa: E402

# ------------------------------------------------------------- cost schedule
# Reused verbatim from clean_room/backtest_index_scalps.py -- do not invent
# new numbers, per the batch brief.
BROKERAGE_ROUND_TRIP = 40.0     # Rs 20 x 2 orders
STT_SELL_RATE = 0.0002          # 0.02% of sell-side notional (futures rate)
EXCHANGE_TXN_RATE = 0.000019
GST_RATE = 0.18
STAMP_DUTY_RATE = 0.00002       # buy-side
TICK = 0.05
LOT = E.NIFTY_LOT               # 50, constant across 2022-2023

WARMUP_START = "2020-01-01T00:00:00+05:30"


def cost_points(price: float, lot: int, slip: float) -> float:
    notional = price * lot
    txn = EXCHANGE_TXN_RATE * notional * 2
    charges = (BROKERAGE_ROUND_TRIP + STT_SELL_RATE * notional + txn
               + STAMP_DUTY_RATE * notional
               + GST_RATE * (BROKERAGE_ROUND_TRIP + txn))
    return charges / lot + 2 * slip


def size_lots(capital: float, risk_pct: float, risk_points: float, lot: int = LOT) -> int:
    """floor((risk_amount / index_points_risk) / lot_size). Returns 0 (a
    'qty_zero' skip, per the batch brief -- no forced floor here) when the
    risk distance is non-positive or too small to buy even 1 lot."""
    if risk_points is None or risk_points <= 0:
        return 0
    risk_amount = capital * risk_pct
    return int((risk_amount / risk_points) // lot)


# ------------------------------------------------------------------ daily bars
def daily_bars(bars_1m):
    """Session-filtered 1-min bars -> daily OHLC dicts, keeping the minute-
    index range [lo, hi) of each day in the SAME 1-min array for fills.
    open=first minute's open, high=day max, low=day min, close=last minute's
    close, per the batch brief."""
    days = E.day_index(bars_1m)
    out = []
    for d, lo, hi in days:
        day_bars = bars_1m[lo:hi]
        out.append({
            "date": d,
            "open": day_bars[0][1],
            "high": max(b[2] for b in day_bars),
            "low": min(b[3] for b in day_bars),
            "close": day_bars[-1][4],
            "lo": lo, "hi": hi,
        })
    return out


def load_daily(con):
    """(bars_1m, db) over [WARMUP_START, E.WINDOW_END) -- see module
    docstring for why the extra pre-window history is loaded."""
    bars = E.filter_session(E.load_index_bars(con, start=WARMUP_START, end=E.WINDOW_END))
    return bars, daily_bars(bars)


def window_start_idx(db, start_date: date = E.WINDOW_START_DATE) -> int:
    """First daily_bars index whose date is >= start_date -- signals before
    this are warm-up only, never traded."""
    for i, d in enumerate(db):
        if d["date"] >= start_date:
            return i
    return len(db)


def daily_atr(db, period: int = 14):
    highs = [d["high"] for d in db]
    lows = [d["low"] for d in db]
    closes = [d["close"] for d in db]
    return atr_series(highs, lows, closes, period)


# --------------------------------------------------------------------- fills
def first_minute_fill(bars_1m, db, idx: int):
    """(timestamp, close) of the FIRST session-minute of daily_bars[idx] --
    the default next-day fill. None if idx is out of range."""
    if idx < 0 or idx >= len(db):
        return None
    ts, o, h, l, c = bars_1m[db[idx]["lo"]]
    return ts, c


def last_minute_fill(bars_1m, db, idx: int):
    """(timestamp, close) of the LAST session-minute of daily_bars[idx] --
    equal to db[idx]['close'] by construction, but sourced from the 1-minute
    series. Used for same-day-close fills and the held-to-dayend mark."""
    if idx < 0 or idx >= len(db):
        return None
    ts, o, h, l, c = bars_1m[db[idx]["hi"] - 1]
    return ts, c


# --------------------------------------------------------------------- trade
def make_trade(strategy: str, side: str, db, bars_1m,
                entry_idx: int, entry_ts, entry_price: float,
                exit_idx: int, exit_ts, exit_price: float,
                reason: str, qty_lots: int, risk_points: float | None,
                slip: float = TICK, notes: str = ""):
    """Build an E.Trade for one long/short index-points round trip, futures
    cost schedule, with the held-to-dayend counterfactual auto-computed
    whenever exit_ts is earlier than that exit day's own last session
    minute (i.e. whenever the exit did NOT already happen at day-end)."""
    gross = (exit_price - entry_price) if side == "long" else (entry_price - exit_price)
    cost = cost_points(entry_price, LOT, slip)
    net_points = gross - cost
    gross_rupees = gross * LOT * qty_lots
    net_rupees = net_points * LOT * qty_lots
    charges_rupees = cost * LOT * qty_lots
    risk_rupees = (risk_points * LOT * qty_lots) if risk_points else 0.0

    tr = E.Trade(
        strategy=strategy, day=db[entry_idx]["date"], entry_at=entry_ts, exit_at=exit_ts,
        direction=side,
        legs=[{"type": "INDEX", "strike": None, "action": "BUY" if side == "long" else "SELL",
               "entry_premium": round(entry_price, 2), "exit_premium": round(exit_price, 2),
               "net_rupees": round(net_rupees, 2)}],
        qty_lots=qty_lots, lot_size=LOT, reason=reason,
        gross_rupees=gross_rupees, net_rupees=net_rupees, charges_rupees=charges_rupees,
        risk_rupees=risk_rupees, notes=notes,
    )

    eod = last_minute_fill(bars_1m, db, exit_idx)
    if eod is not None and eod[0] > exit_ts:
        eod_ts, eod_price = eod
        eod_gross = (eod_price - entry_price) if side == "long" else (entry_price - eod_price)
        eod_net = (eod_gross - cost) * LOT * qty_lots
        tr.held_to_dayend_net_rupees = eod_net
        tr.held_to_dayend_note = f"marked at {eod_ts.time()} close {eod_price}"
    return tr


def mean(xs):
    return statistics.mean(xs) if xs else None
