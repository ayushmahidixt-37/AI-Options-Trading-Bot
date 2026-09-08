"""Shared engine for the 2022-2023 strategy backtests in this folder.

Every strategy script in strategies/ imports from here so the economics
(costs, position sizing, metrics) are identical across all of them -- the
only thing that should differ between strategy files is the signal logic.

DATA: data_ingest/data/nifty_forward.sqlite3. Underlying coverage is NIFTY
only -- there is no BANKNIFTY row anywhere in `instruments` (checked
directly: `SELECT DISTINCT underlying FROM instruments` returns exactly one
row, 'NIFTY'). Strategies #8 and #12 are specified on BANKNIFTY; they run
here on NIFTY instead with that substitution flagged loudly in their own
output, because fabricating BankNifty numbers would be worse than being
explicit about the gap.

EXECUTION MODEL for the six directional strategies (#1-6): signal, stop and
target are computed on the NIFTY *index* 1-minute series, exactly as the
spec defines them (VWAP/EMA/Supertrend/RSI act on "price", and the index is
the instrument those indicators were written against). The P&L that is
actually reported, though, comes from a real ATM option: CE on every long
signal, PE on every short signal, priced off real `market_candles` rows for
that option token. This answers "if we had bought that call/put" rather than
reporting index points. Position sizing needs a stop *in premium terms*; the
index-level stop distance is converted to an approximate premium distance
via Black-Scholes delta (computed from the stored IV, same technique already
used in clean_room/backtest_iron_condor.py) with a 0.5 fallback when IV is
missing. That delta is a sizing assumption only -- the P&L itself always
uses the real observed premium at entry and exit, never a delta estimate.

COSTS: brokerage Rs 20/order, NSE exchange transaction charge 0.03503% of
premium turnover (the rate this repo already used in backtest_iron_condor.py
before this file existed), 18% GST on (brokerage + exchange charge), stamp
duty 0.003% of buy-side premium, and STT on the sell side of every option
leg. STT rate is date-dependent and this backtest window straddles the one
change that matters: 0.05% of premium through 2023-03-31, 0.0625% from
2023-04-01 (Budget 2023; confirmed date of the hike). Slippage is >=1 tick
(0.05) applied against the trader on both entry and exit of every leg, per
the file's "Shared backtest requirements".

POSITION SIZING: risk_amount = capital x risk_%; quantity = risk_amount /
|entry_price - stop_price|, rounded down to whole lots, per the file's
formula. capital=Rs 1,00,000. NIFTY lot size for the entire 2022-2023 window
is 50 (dhan_data.nifty_lot_size only changes lot size starting 2024-04-26),
so this is a constant in practice here, not a schedule lookup.

COUNTERFACTUAL: every trade that closes before its instrument's last traded
minute of that calendar day also records what the position would have been
worth at that day's last available price for the same token(s), had it NOT
been closed at the actual exit. This is the "if we had not exited, how would
it have ended that day" figure the brief asked for, computed for every
strategy, not just a subset.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from options_bot.dhan_data import nifty_lot_size  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
DB = REPO / "data_ingest" / "data" / "nifty_forward.sqlite3"
INDEX_TOKEN = "NSE_INDEX|Nifty 50"
CAPITAL = 100_000.0
TICK = 0.05
RISK_FREE = 0.065  # assumption, not data -- matches backtest_iron_condor.py

WINDOW_START = "2022-01-01T00:00:00+05:30"
WINDOW_END = "2024-01-01T00:00:00+05:30"
WINDOW_START_DATE = date(2022, 1, 1)
WINDOW_END_DATE = date(2023, 12, 31)
# The original two-year window (matches REPORT.md/PHASE2_REPORT.md).

BROKERAGE_PER_ORDER = 20.0
EXCH_TXN_RATE = 0.0003503
GST_RATE = 0.18
STAMP_RATE_BUY = 0.00003


def stt_sell_rate(d: date) -> float:
    """Options STT, sell side, % of premium. Hiked 2023-04-01 (Budget 2023)."""
    return 0.0005 if d < date(2023, 4, 1) else 0.000625


# --------------------------------------------------------------------- data
def connect(db: Path | str = DB) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def load_index_bars(con: sqlite3.Connection, timeframe: str = "ONE_MINUTE",
                     start: str = WINDOW_START, end: str = WINDOW_END):
    rows = con.execute(
        "SELECT started_at,open,high,low,close FROM market_candles "
        "WHERE instrument_token=? AND timeframe=? AND started_at>=? AND started_at<? "
        "ORDER BY started_at",
        (INDEX_TOKEN, timeframe, start, end)).fetchall()
    return [(datetime.fromisoformat(t), float(o), float(h), float(l), float(c))
            for t, o, h, l, c in rows]


SESSION_START = time(9, 15)
SESSION_END = time(15, 30)


def filter_session(bars):
    """Keep only real trading-session bars.

    The archive carries a padded row for every clock minute outside NSE
    hours too (checked directly: 2022-01-03 has 676 rows for one date, not
    ~375 -- rows from 07:06 through 09:14 and from 15:31 through 18:21 are
    flat O=H=L=C, exactly the prior real print carried forward). Computing
    any indicator over that padding would be wrong (RSI/EMA would see zero
    "movement" through hours that were never open), so every signal in this
    folder is computed on session-filtered bars only, never the raw table.
    """
    return [b for b in bars if SESSION_START <= b[0].time() <= SESSION_END]


def day_index(bars):
    """Map bars to per-session (date, start_idx, end_idx) ranges."""
    days, cur, start = [], None, 0
    for i, b in enumerate(bars):
        d = b[0].date()
        if d != cur:
            if cur is not None:
                days.append((cur, start, i))
            cur, start = d, i
    if cur is not None:
        days.append((cur, start, len(bars)))
    return days


def list_expiries(con, start=WINDOW_START_DATE, end=WINDOW_END_DATE):
    rows = con.execute(
        "SELECT DISTINCT expiry FROM instruments WHERE expiry>=? AND expiry<=? ORDER BY expiry",
        (start.isoformat(), end.isoformat())).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def nearest_expiry_on_or_after(con, day: date, all_expiries=None):
    if all_expiries is None:
        rows = con.execute("SELECT DISTINCT expiry FROM instruments WHERE expiry>=? ORDER BY expiry LIMIT 1",
                            (day.isoformat(),)).fetchall()
        return date.fromisoformat(rows[0][0]) if rows else None
    for e in all_expiries:
        if e >= day:
            return e
    return None


def option_token_at(con, expiry: date, strike: float, option_type: str):
    row = con.execute(
        "SELECT token FROM instruments WHERE expiry=? AND strike=? AND option_type=? LIMIT 1",
        (expiry.isoformat(), strike, option_type)).fetchone()
    return row[0] if row else None


def option_series(con, token: str, start_iso: str, end_iso: str):
    """1-minute (started_at, close, iv) rows for one option token, inclusive."""
    rows = con.execute(
        "SELECT started_at,close,implied_volatility FROM market_candles "
        "WHERE instrument_token=? AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=? "
        "ORDER BY started_at", (token, start_iso, end_iso)).fetchall()
    return [(datetime.fromisoformat(t), float(c), (float(iv) if iv is not None else None))
            for t, c, iv in rows]


def price_at_or_before(con, token: str, ts_iso: str):
    row = con.execute(
        "SELECT started_at,close,implied_volatility FROM market_candles "
        "WHERE instrument_token=? AND timeframe='ONE_MINUTE' AND started_at<=? "
        "ORDER BY started_at DESC LIMIT 1", (token, ts_iso)).fetchone()
    if row is None:
        return None
    t, c, iv = row
    return (datetime.fromisoformat(t), float(c), (float(iv) if iv is not None else None))


def price_at_or_after(con, token: str, ts_iso: str):
    row = con.execute(
        "SELECT started_at,close,implied_volatility FROM market_candles "
        "WHERE instrument_token=? AND timeframe='ONE_MINUTE' AND started_at>=? "
        "ORDER BY started_at ASC LIMIT 1", (token, ts_iso)).fetchone()
    if row is None:
        return None
    t, c, iv = row
    return (datetime.fromisoformat(t), float(c), (float(iv) if iv is not None else None))


def day_end_price(con, token: str, day: date):
    """Last real-session print of `token` on `day` (09:15-15:30 only -- see
    filter_session) -- the counterfactual "if we had not exited" mark."""
    row = con.execute(
        "SELECT started_at,close FROM market_candles WHERE instrument_token=? "
        "AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=? "
        "ORDER BY started_at DESC LIMIT 1",
        (token, f"{day.isoformat()}T09:15:00+05:30", f"{day.isoformat()}T15:30:00+05:30")).fetchone()
    if row is None:
        return None
    t, c = row
    return (datetime.fromisoformat(t), float(c))


def atm_strike(spot: float, step: float = 50.0) -> float:
    return round(spot / step) * step


# ---------------------------------------------------------------- black-scholes
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(spot, strike, t_years, iv_pct, option_type):
    """Black-Scholes delta. iv_pct is IV in percent, as stored in the archive."""
    if t_years is None or t_years <= 0 or iv_pct is None or iv_pct <= 0 or spot <= 0 or strike <= 0:
        return None
    sigma = iv_pct / 100.0
    d1 = ((math.log(spot / strike) + (RISK_FREE + 0.5 * sigma * sigma) * t_years)
          / (sigma * math.sqrt(t_years)))
    return _norm_cdf(d1) if option_type == "CE" else _norm_cdf(d1) - 1.0


def _bs_price(spot, strike, t_years, sigma, option_type):
    if t_years <= 0 or sigma <= 0:
        return max(0.0, (spot - strike) if option_type == "CE" else (strike - spot))
    d1 = ((math.log(spot / strike) + (RISK_FREE + 0.5 * sigma * sigma) * t_years)
          / (sigma * math.sqrt(t_years)))
    d2 = d1 - sigma * math.sqrt(t_years)
    if option_type == "CE":
        return spot * _norm_cdf(d1) - strike * math.exp(-RISK_FREE * t_years) * _norm_cdf(d2)
    return strike * math.exp(-RISK_FREE * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(price, spot, strike, t_years, option_type, tol=1e-4, max_iter=60):
    """Invert Black-Scholes by bisection: given a real traded premium (e.g.
    a live quote, where -- unlike this backtest's archive -- no IV field is
    available), recover the IV that produces it, for use with bs_delta.
    Bisection rather than Newton: no derivative needed, and it can't
    diverge, which matters more than speed for the handful of calls/cycle
    this is used for live. Returns None if the price is outside any
    achievable range (e.g. below intrinsic) rather than returning a
    nonsense value."""
    if t_years <= 0 or spot <= 0 or strike <= 0 or price <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if option_type == "CE" else (strike - spot))
    if price < intrinsic - 1e-6:
        return None
    lo, hi = 1e-4, 5.0  # 0.01% to 500% annualized vol -- generous bracket
    if _bs_price(spot, strike, t_years, hi, option_type) < price:
        return None  # price implies vol beyond the bracket; refuse rather than guess
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        est = _bs_price(spot, strike, t_years, mid, option_type)
        if abs(est - price) < tol:
            return mid * 100.0  # percent, matching bs_delta's stored-IV convention
        if est < price:
            lo = mid
        else:
            hi = mid
    return mid * 100.0


def years_to_expiry(expiry: date, at: datetime) -> float:
    expiry_dt = datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=IST)
    return max((expiry_dt - at).total_seconds(), 0.0) / (365 * 24 * 3600)


# --------------------------------------------------------------------- costs
def leg_cost_rupees(entry_prem, exit_prem, lot_size, qty_lots, slip, trade_date,
                     action: str):
    """Round-trip cost in rupees for ONE leg, `action` = 'BUY' (open long) or
    'SELL' (open short). Slippage moves both entry and exit against the
    trader by `slip` premium points; STT applies to whichever fill is a
    market sell (exit of a long, or entry of a short)."""
    units = lot_size * qty_lots
    if action == "BUY":
        eff_entry, eff_exit = entry_prem + slip, exit_prem - slip
        sell_notional = eff_exit * units  # closing sell
    else:  # SELL (short open, buy to close)
        eff_entry, eff_exit = entry_prem - slip, exit_prem + slip
        sell_notional = eff_entry * units  # opening sell
    turnover = (eff_entry + eff_exit) * units
    brokerage = BROKERAGE_PER_ORDER * 2
    txn = EXCH_TXN_RATE * turnover
    stt = stt_sell_rate(trade_date) * max(sell_notional, 0.0)
    stamp = STAMP_RATE_BUY * (eff_entry * units if action == "BUY" else 0.0)
    gst = GST_RATE * (brokerage + txn)
    charges = brokerage + txn + stt + stamp + gst
    slip_cost = 0.0  # slip already folded into eff_entry/eff_exit above
    price_pnl = (eff_exit - eff_entry) * units if action == "BUY" else (eff_entry - eff_exit) * units
    return price_pnl - charges, charges


# ----------------------------------------------------------------- sizing
def size_by_index_risk(capital, risk_pct, index_risk_pts, delta_abs, lot_size):
    """Convert an index-point stop distance to a premium-risk-based lot count
    via |delta|. Falls back to delta=0.5 (ATM-ish) when delta is unknown."""
    d = abs(delta_abs) if delta_abs else 0.5
    premium_risk = max(index_risk_pts * d, TICK)
    return size_by_premium_risk(capital, risk_pct, premium_risk, lot_size)


def size_by_premium_risk(capital, risk_pct, premium_risk, lot_size):
    if premium_risk <= 0:
        return 0
    risk_amount = capital * risk_pct
    lots = int((risk_amount / premium_risk) // lot_size)
    return max(lots, 0) * lot_size // lot_size  # lots count, not units


NIFTY_LOT = 50  # constant across 2022-2023; see module docstring


def size_or_floor(capital, risk_pct, risk_per_unit_rupees, lot_size, allow_floor=True):
    """Lots from the file's risk formula; if that rounds to zero (a defined-
    risk structure whose one-lot max loss already exceeds the risk budget --
    happens routinely for wide-wing spreads on a Rs 1L account), optionally
    floor to 1 lot so the strategy still produces a P&L series to judge,
    clearly marked as exceeding the target risk. Returns
    (qty_lots, floored, actual_risk_pct_of_capital).

    IMPORTANT -- `risk_per_unit_rupees` is PER UNIT (per single share/point
    of premium, e.g. the option's own points-of-risk, unscaled), NOT per
    lot. This function multiplies by `lot_size` itself. Passing an
    already-per-lot value here (risk_per_unit * lot_size, computed by the
    caller) is a real, previously-shipped bug: it silently double-divides
    by lot_size, which (a) over-triggers the floor for structures whose true
    risk would actually have sized a real, non-floored lot count, and (b)
    for the tightest-stop trades, undersizes a position that could truthfully
    have afforded 2+ lots. If you're computing `max_loss * lot_size` right
    before calling this, that's the bug -- pass `max_loss` alone."""
    if risk_per_unit_rupees <= 0:
        return 0, False, 0.0
    risk_amount = capital * risk_pct
    qty_lots = int((risk_amount / risk_per_unit_rupees) // lot_size) * lot_size // lot_size
    floored = False
    if qty_lots <= 0 and allow_floor:
        qty_lots, floored = 1, True
    actual_risk_pct = (risk_per_unit_rupees * lot_size * max(qty_lots, 0)) / capital * 100
    return qty_lots, floored, actual_risk_pct


# --------------------------------------------------- index signal simulation
def twap_for(bars, lo, hi):
    """Session TWAP, substituting for VWAP -- the index has no volume."""
    out, tot = [], 0.0
    for k, i in enumerate(range(lo, hi)):
        _, _, h, l, c = bars[i]
        tot += (h + l + c) / 3
        out.append(tot / (k + 1))
    return out


def wilder_atr(bars, period):
    n = len(bars)
    trs = []
    for i in range(n):
        _, o, h, l, c = bars[i]
        pc = bars[i - 1][4] if i else c
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = [None] * n
    if n < period:
        return atr
    run = sum(trs[:period]) / period
    atr[period - 1] = run
    for i in range(period, n):
        run = (run * (period - 1) + trs[i]) / period
        atr[i] = run
    return atr


def supertrend(bars, period=10, mult=3.0):
    atr = wilder_atr(bars, period)
    n = len(bars)
    line, direction = [None] * n, [0] * n
    fub = flb = None
    prev_dir = 1
    for i in range(n):
        if atr[i] is None:
            continue
        _, o, h, l, c = bars[i]
        mid = (h + l) / 2
        ub, lb = mid + mult * atr[i], mid - mult * atr[i]
        pc = bars[i - 1][4] if i else c
        fub = ub if (fub is None or ub < fub or pc > fub) else fub
        flb = lb if (flb is None or lb > flb or pc < flb) else flb
        if prev_dir == 1:
            d = -1 if c < flb else 1
        else:
            d = 1 if c > fub else -1
        direction[i], line[i] = d, (flb if d == 1 else fub)
        prev_dir = d
    return line, direction


@dataclass
class Signal:
    """A raw index-level entry/exit before it is turned into a real option
    trade. `entry_index`/`stop_index` size the position; `exit_index_price`
    is unused for P&L (the option premium is), only kept for debugging."""
    day: date
    side: str            # "long" or "short"
    entry_at: datetime
    entry_index: float
    stop_index: float
    exit_at: datetime
    reason: str


def walk_signal(bars, day, i, side, entry_index, stop_index, end, target=None,
                 exit_fn=None, trail_fn=None):
    """Walk forward on INDEX bars to stop/target/signal-exit/session-end.
    Returns (Signal, next_index). Ties (stop and target same bar) assume the
    stop fills first, the conservative assumption."""
    entry_at = bars[i][0]
    cur = stop_index
    for j in range(i + 1, end):
        ts, o, h, l, c = bars[j]
        if trail_fn is not None:
            t = trail_fn(j)
            if t is not None:
                cur = max(cur, t) if side == "long" else min(cur, t)
        if (side == "long" and l <= cur) or (side == "short" and h >= cur):
            return Signal(day, side, entry_at, entry_index, stop_index, ts, "stop"), j
        if target is not None:
            tgt = target(j) if callable(target) else target
            if tgt is not None:
                if side == "long" and h >= tgt:
                    return Signal(day, side, entry_at, entry_index, stop_index, ts, "target"), j
                if side == "short" and l <= tgt:
                    return Signal(day, side, entry_at, entry_index, stop_index, ts, "target"), j
        if exit_fn is not None and exit_fn(j):
            return Signal(day, side, entry_at, entry_index, stop_index, ts, "signal"), j
    ts = bars[end - 1][0]
    return Signal(day, side, entry_at, entry_index, stop_index, ts, "session_end"), end - 1


# --------------------------------------------------- directional option trade
def build_directional_option_trade(con, strategy_name, day, side, entry_at, exit_at,
                                    exit_reason, index_entry, index_stop, slip=TICK,
                                    risk_pct=0.02, all_expiries=None, strike_step=50.0):
    """One long CE (side='long') or long PE (side='short') trade, realized
    on real option premiums, sized from the index-level stop distance via
    BS delta. Returns (Trade, None) or (None, skip_reason)."""
    expiry = nearest_expiry_on_or_after(con, day, all_expiries)
    if expiry is None:
        return None, "no_expiry"
    option_type = "CE" if side == "long" else "PE"
    strike = atm_strike(index_entry, strike_step)
    token = option_token_at(con, expiry, strike, option_type)
    if token is None:
        return None, "no_instrument"

    entry_bar = price_at_or_after(con, token, entry_at.isoformat())
    if entry_bar is None:
        return None, "no_entry_price"
    entry_ts, entry_prem, entry_iv = entry_bar
    if entry_prem <= 0:
        return None, "zero_entry_premium"

    exit_bar = price_at_or_after(con, token, exit_at.isoformat())
    if exit_bar is None:
        exit_bar = price_at_or_before(con, token, exit_at.isoformat())
    if exit_bar is None:
        return None, "no_exit_price"
    exit_ts, exit_prem, _ = exit_bar

    yrs = years_to_expiry(expiry, entry_ts)
    delta = bs_delta(index_entry, strike, yrs, entry_iv, option_type)
    index_risk = abs(index_entry - index_stop)
    qty_lots = size_by_index_risk(CAPITAL, risk_pct, index_risk, delta, NIFTY_LOT)
    if qty_lots <= 0:
        return None, "qty_zero"

    net, charges = leg_cost_rupees(entry_prem, exit_prem, NIFTY_LOT, qty_lots, slip, day, "BUY")
    gross = (exit_prem - entry_prem) * NIFTY_LOT * qty_lots

    tr = Trade(strategy=strategy_name, day=day, entry_at=entry_ts, exit_at=exit_ts,
               direction=f"long_{option_type.lower()}",
               legs=[{"type": option_type, "strike": strike, "action": "BUY",
                      "entry_premium": entry_prem, "exit_premium": exit_prem,
                      "net_rupees": round(net, 2)}],
               qty_lots=qty_lots, lot_size=NIFTY_LOT, reason=exit_reason,
               gross_rupees=gross, net_rupees=net, charges_rupees=charges,
               risk_rupees=index_risk * (abs(delta) if delta else 0.5) * NIFTY_LOT * qty_lots,
               notes=f"expiry={expiry} strike={strike} delta~{delta}")

    if exit_reason != "session_end":
        eod = day_end_price(con, token, day)
        if eod is not None and eod[0] > exit_ts:
            eod_ts, eod_prem = eod
            eod_net, _ = leg_cost_rupees(entry_prem, eod_prem, NIFTY_LOT, qty_lots, slip, day, "BUY")
            tr.held_to_dayend_net_rupees = eod_net
            tr.held_to_dayend_note = f"marked at {eod_ts.time()} close {eod_prem}"
    return tr, None


# --------------------------------------------------------------------- trade
@dataclass
class Trade:
    strategy: str
    day: date
    entry_at: datetime
    exit_at: datetime | None = None
    direction: str = ""          # long_call / long_put / short_structure / ...
    legs: list = field(default_factory=list)   # [{"type","strike","action","entry","exit"}]
    qty_lots: int = 0
    lot_size: int = NIFTY_LOT
    reason: str = ""
    gross_rupees: float = 0.0
    net_rupees: float = 0.0
    charges_rupees: float = 0.0
    held_to_dayend_net_rupees: float | None = None
    held_to_dayend_note: str = ""
    risk_rupees: float = 0.0
    notes: str = ""

    def as_row(self):
        return {
            "strategy": self.strategy, "day": self.day.isoformat(),
            "entry_at": self.entry_at.isoformat(),
            "exit_at": self.exit_at.isoformat() if self.exit_at else None,
            "direction": self.direction, "legs": self.legs,
            "qty_lots": self.qty_lots, "lot_size": self.lot_size,
            "reason": self.reason, "gross_rupees": round(self.gross_rupees, 2),
            "net_rupees": round(self.net_rupees, 2),
            "charges_rupees": round(self.charges_rupees, 2),
            "held_to_dayend_net_rupees": (
                round(self.held_to_dayend_net_rupees, 2)
                if self.held_to_dayend_net_rupees is not None else None),
            "held_to_dayend_note": self.held_to_dayend_note,
            "risk_rupees": round(self.risk_rupees, 2), "notes": self.notes,
        }


# ------------------------------------------------------------------ metrics
def compute_metrics(trades: list[Trade]):
    if not trades:
        return None
    nets = [t.net_rupees for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    total_net = sum(nets)

    by_day: dict[date, float] = {}
    for t in trades:
        d = (t.exit_at or t.entry_at).date()
        by_day[d] = by_day.get(d, 0.0) + t.net_rupees
    ordered_days = sorted(by_day)
    equity, peak, max_dd, max_dd_pct = CAPITAL, CAPITAL, 0.0, 0.0
    daily_returns = []
    for d in ordered_days:
        equity += by_day[d]
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd / peak if peak > 0 else 0.0)
        daily_returns.append(by_day[d] / CAPITAL)

    span_days = (ordered_days[-1] - ordered_days[0]).days if len(ordered_days) > 1 else 1
    years = max(span_days / 365.0, 1 / 365.0)
    final_equity = CAPITAL + total_net
    cagr = (final_equity / CAPITAL) ** (1 / years) - 1 if final_equity > 0 else -1.0

    if len(daily_returns) > 1 and statistics.pstdev(daily_returns) > 0:
        sharpe = (statistics.mean(daily_returns) / statistics.pstdev(daily_returns)) * math.sqrt(252)
    else:
        sharpe = 0.0

    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) else float("inf")

    return {
        "n_trades": len(trades),
        "win_rate_pct": 100.0 * len(wins) / len(trades),
        "gross_total_rupees": round(sum(t.gross_rupees for t in trades), 2),
        "net_total_rupees": round(total_net, 2),
        "avg_net_per_trade": round(total_net / len(trades), 2),
        "avg_win": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.mean(losses), 2) if losses else 0.0,
        "profit_factor": (round(pf, 3) if pf != float("inf") else None),
        "max_drawdown_rupees": round(max_dd, 2),
        "max_drawdown_pct_of_peak": round(100 * max_dd_pct, 2),
        "cagr_pct": round(100 * cagr, 2),
        "sharpe": round(sharpe, 3),
        "final_equity": round(final_equity, 2),
        "trading_days_active": len(ordered_days),
        "date_span": f"{ordered_days[0]}..{ordered_days[-1]}" if ordered_days else "",
    }


def held_to_dayend_summary(trades: list[Trade]):
    early = [t for t in trades if t.held_to_dayend_net_rupees is not None]
    if not early:
        return None
    actual = sum(t.net_rupees for t in early)
    counter = sum(t.held_to_dayend_net_rupees for t in early)
    better_to_hold = sum(1 for t in early if t.held_to_dayend_net_rupees > t.net_rupees)
    return {
        "n_early_exits": len(early),
        "actual_net_rupees": round(actual, 2),
        "if_held_to_dayend_net_rupees": round(counter, 2),
        "delta_rupees": round(counter - actual, 2),
        "pct_where_holding_wouldve_been_better": round(100 * better_to_hold / len(early), 1),
    }


def leg_side_breakdown(trades: list[Trade]):
    """Split net P&L / win-rate by option side (CE vs PE), across every leg
    of every trade -- answers 'check both call and put, every strategy'."""
    sides = {"CE": [], "PE": []}
    for t in trades:
        for leg in t.legs:
            ot = leg.get("type")
            if ot in sides and leg.get("net_rupees") is not None:
                sides[ot].append(leg["net_rupees"])
    out = {}
    for ot, vals in sides.items():
        if not vals:
            out[ot] = None
            continue
        wins = [v for v in vals if v > 0]
        out[ot] = {
            "n": len(vals), "win_rate_pct": round(100 * len(wins) / len(vals), 1),
            "net_rupees": round(sum(vals), 2), "avg_rupees": round(sum(vals) / len(vals), 2),
        }
    return out


# --------------------------------------------------------------- breakdowns
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _bucket(trades, keyfn):
    out: dict = {}
    for t in trades:
        k = keyfn(t)
        out.setdefault(k, []).append(t.net_rupees)
    rows = {}
    for k, vals in out.items():
        wins = [v for v in vals if v > 0]
        rows[k] = {"n": len(vals), "win_rate_pct": round(100 * len(wins) / len(vals), 1),
                    "net_rupees": round(sum(vals), 2), "avg_rupees": round(sum(vals) / len(vals), 2)}
    return rows


def by_weekday(trades):
    r = _bucket(trades, lambda t: WEEKDAY_NAMES[t.entry_at.weekday()])
    return {d: r[d] for d in WEEKDAY_NAMES if d in r}


def by_month(trades):
    r = _bucket(trades, lambda t: t.entry_at.strftime("%Y-%m"))
    return dict(sorted(r.items()))


def by_year(trades):
    r = _bucket(trades, lambda t: t.entry_at.year)
    return dict(sorted(r.items()))


def by_hour(trades):
    r = _bucket(trades, lambda t: t.entry_at.strftime("%H:00"))
    return dict(sorted(r.items()))


# ------------------------------------------------------------------------ io
def save_results(name: str, trades: list[Trade], extra: dict | None = None,
                  results_dir: Path | None = None):
    import json
    results_dir = results_dir or (Path(__file__).resolve().parent / "results")
    results_dir.mkdir(exist_ok=True)
    payload = {
        "strategy": name,
        "window": f"{WINDOW_START_DATE}..{WINDOW_END_DATE}",
        "capital": CAPITAL,
        "metrics": compute_metrics(trades),
        "held_to_dayend": held_to_dayend_summary(trades),
        "leg_side_breakdown": leg_side_breakdown(trades),
        "by_weekday": by_weekday(trades),
        "by_month": by_month(trades),
        "by_year": by_year(trades),
        "by_hour": by_hour(trades),
        "extra": extra or {},
        "trades": [t.as_row() for t in trades],
    }
    out = results_dir / f"{name}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out, payload
