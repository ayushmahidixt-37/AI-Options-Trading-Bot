"""#9 Bull Put Credit Spread -- NIFTY monthly, support-based, spec item #9.

This one is DIRECTIONAL (mildly bullish) and signal-based, not a fixed
clock-time entry: it fires off a daily support/trend read on the index.

Spec implemented:
  Signal    spot closes above its 20-day SMA on the DAILY candle AND is
            within 1% of a prior swing-low support level
  Entry     next trading day, 09:30
  Structure short put at the nearest strike below the support level; long
            put hedge 100 points further OTM. NIFTY MONTHLY expiries only.
  Stop      spot closes below the long put strike on a daily candle
            (signal exit, checked once per day close, not intraday)
  Target    50% of max profit (net credit), monitored on the real premium
            series intraday (this part IS a live MTM check, like every
            other option-selling strategy in this batch -- the spec gives
            no reason to believe the 50%-of-credit target is meant to be
            checked only at day close)
  Time      close 3 trading days before monthly expiry

JUDGMENT CALL -- daily series construction: built by aggregating
session-filtered 1-min index bars into one OHLC per real trading day
(`E.day_index` over `E.filter_session` bars), skipping any day whose
session shows zero price range -- the archive pads flat rows across whole
Saturdays and is simply silent on weekday holidays (verified directly:
2022-01-08/2022-01-22, both Saturdays, show one constant close across the
would-be session; 2022-01-26, a Wednesday holiday, has zero rows). Without
this filter, Saturdays would show up as extra zero-range "trading days" in
the daily series and corrupt the SMA/swing-low computation.

JUDGMENT CALL -- 20-day SMA: mean of the trailing 20 REAL trading days'
closes, inclusive of the current day (SMA(20) as of and including today's
close).

JUDGMENT CALL -- swing-low definition (spec explicitly leaves this open):
day D is a local minimum if close[D] equals the minimum close over the
window [D-5, D+5] (11 real trading days, D itself included). To avoid
look-ahead bias in the actual trading signal (a swing low is only
knowable in hindsight once the 5 days AFTER it have printed), a swing low
at day D only counts as a "prior swing-low support" for a signal evaluated
on day T if D+5 <= T, i.e. it is fully confirmed using data that would
genuinely have been available by day T. "Within 1% of a prior swing-low"
means today's close is within 1% of the CLOSE PRICE at any such confirmed
swing-low day (support = that close level).

JUDGMENT CALL -- support level -> strike: short put strike = nearest
50-point strike at or below the swing-low support price
(floor(support/50)*50); long put hedge = short_strike - 100, per spec.

JUDGMENT CALL -- NIFTY MONTHLY expiry filter: `E.list_expiries` returns
every weekly Thursday. This script keeps only the LAST expiry of each
calendar month present in that list (an expiry is "monthly" if the next
expiry chronologically falls in a different month, or there is no later
expiry in the window) -- the standard "last Thursday of the month" reading
without needing an explicit monthly/weekly instrument flag.

JUDGMENT CALL -- time exit granularity: "close 3 trading days before
monthly expiry" gives no clock time. This script force-exits at that
trading day's LAST AVAILABLE minute price for both legs (i.e. that day's
session close), which is the natural reading of a day-granularity exit
rule and mirrors `E.day_end_price`'s own convention.

JUDGMENT CALL -- lookback data: the daily series is built from index bars
starting well before WINDOW_START_DATE (2021-10-01) purely to give the
20-day SMA and +/-5-day swing-low window enough history at the very start
of 2022; only signals whose ENTRY day falls inside
[WINDOW_START_DATE, WINDOW_END_DATE] are ever traded.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s09_bull_put_spread"
SMA_PERIOD = 20
SWING_WINDOW = 5             # +/- days
SUPPORT_TOLERANCE = 0.01     # within 1% of support
HEDGE_OFFSET = 100.0
TAKE_PROFIT = 0.50
TIME_EXIT_DAYS_BEFORE_EXPIRY = 3
STRIKE_STEP = 50.0
LOOKBACK_START = "2021-10-01T00:00:00+05:30"


def daily_bars(bars):
    """One (date, o, h, l, c) row per REAL trading day -- see module
    docstring for why zero-range days (padded Saturdays) are dropped."""
    out = []
    for d, lo, hi in E.day_index(bars):
        chunk = bars[lo:hi]
        o = chunk[0][1]
        h = max(b[2] for b in chunk)
        l = min(b[3] for b in chunk)
        c = chunk[-1][4]
        if h - l <= 1e-6:
            continue
        out.append((d, o, h, l, c))
    return out


def monthly_expiries(all_expiries):
    out = []
    for i, e in enumerate(all_expiries):
        nxt = all_expiries[i + 1] if i + 1 < len(all_expiries) else None
        if nxt is None or (nxt.year, nxt.month) != (e.year, e.month):
            out.append(e)
    return out


def find_signals(daily):
    """Returns list of (signal_day_idx, support_price) for entry-eligible
    days. `daily` is sorted ascending by date."""
    n = len(daily)
    closes = [row[4] for row in daily]

    # Confirmed swing lows: day i is a local min over [i-5, i+5], and only
    # usable as "prior support" for day t once t >= i+5 (no look-ahead).
    swing_low_idx = []
    for i in range(SWING_WINDOW, n - SWING_WINDOW):
        window = closes[i - SWING_WINDOW: i + SWING_WINDOW + 1]
        if closes[i] == min(window):
            swing_low_idx.append(i)

    signals = []
    for t in range(SMA_PERIOD - 1, n):
        sma = sum(closes[t - SMA_PERIOD + 1: t + 1]) / SMA_PERIOD
        if closes[t] <= sma:
            continue
        confirmed_supports = [closes[i] for i in swing_low_idx if i + SWING_WINDOW <= t and i < t]
        if not confirmed_supports:
            continue
        nearest_support = min(confirmed_supports, key=lambda s: abs(closes[t] - s))
        if abs(closes[t] - nearest_support) / nearest_support <= SUPPORT_TOLERANCE:
            signals.append((t, nearest_support))
    return signals


def option_chain_prices(con, expiry, strikes, ts):
    """Nearest real print at-or-after `ts` for each strike's PE token, same
    day only. Deep/illiquid strikes routinely have no trade at the exact
    entry minute (unlike the index, option candles are NOT padded -- a
    missing minute means no trade happened, not a carried-forward value),
    so this uses `E.price_at_or_after` per leg instead of an exact-minute
    join."""
    day_end_iso = f"{ts.date().isoformat()}T15:30:00+05:30"
    out = {}
    for strike in strikes:
        tok = E.option_token_at(con, expiry, strike, "PE")
        if tok is None:
            continue
        bar = E.price_at_or_after(con, tok, ts.isoformat())
        if bar is None or bar[0].isoformat() > day_end_iso:
            continue
        out[strike] = (tok, bar[1])
    return out if out else None


def leg_series(con, tokens, start_iso, end_iso):
    marks = ",".join("?" * len(tokens))
    rows = con.execute(
        f"SELECT instrument_token, started_at, close FROM market_candles "
        f"WHERE instrument_token IN ({marks}) AND timeframe='ONE_MINUTE' "
        f"AND started_at>? AND started_at<=? ORDER BY started_at",
        list(tokens) + [start_iso, end_iso]).fetchall()
    out = {}
    for token, ts, close in rows:
        out.setdefault(ts, {})[token] = float(close)
    return out


def run_spread(con, day, entry_at, short_leg, long_leg, credit, time_exit_at, qty_lots, slip):
    tokens = [short_leg["token"], long_leg["token"]]
    series = leg_series(con, tokens, entry_at.isoformat(), time_exit_at.isoformat())

    last_ts, last_short, last_long, reason = None, None, None, "time_exit"
    for ts in sorted(series):
        prices = series[ts]
        if short_leg["token"] not in prices or long_leg["token"] not in prices:
            continue
        last_ts = ts
        last_short, last_long = prices[short_leg["token"]], prices[long_leg["token"]]
        value = last_short - last_long
        profit = credit - value
        if profit >= TAKE_PROFIT * credit:
            reason = "target"
            break
    else:
        pass

    if last_ts is None:
        return None
    exit_ts = datetime.fromisoformat(last_ts)
    exit_day = exit_ts.date()

    short_net, short_charges = E.leg_cost_rupees(short_leg["entry"], last_short, E.NIFTY_LOT,
                                                  qty_lots, slip, exit_day, "SELL")
    long_net, long_charges = E.leg_cost_rupees(long_leg["entry"], last_long, E.NIFTY_LOT,
                                                qty_lots, slip, exit_day, "BUY")
    total_net = short_net + long_net
    total_charges = short_charges + long_charges
    leg_rows = [
        {"type": "PE", "strike": short_leg["strike"], "action": "SELL",
         "entry_premium": short_leg["entry"], "exit_premium": last_short,
         "net_rupees": round(short_net, 2)},
        {"type": "PE", "strike": long_leg["strike"], "action": "BUY",
         "entry_premium": long_leg["entry"], "exit_premium": last_long,
         "net_rupees": round(long_net, 2)},
    ]

    tr = E.Trade(strategy=NAME, day=day, entry_at=entry_at, exit_at=exit_ts,
                 direction="bull_put_spread", legs=leg_rows, qty_lots=qty_lots,
                 lot_size=E.NIFTY_LOT, reason=reason,
                 gross_rupees=total_net + total_charges, net_rupees=total_net,
                 charges_rupees=total_charges,
                 risk_rupees=(HEDGE_OFFSET - credit) * E.NIFTY_LOT * qty_lots,
                 notes=f"short_strike={short_leg['strike']} long_strike={long_leg['strike']} "
                       f"credit={credit:.2f}")

    if reason != "time_exit" or exit_ts < time_exit_at:
        eod_short = E.day_end_price(con, short_leg["token"], exit_day)
        eod_long = E.day_end_price(con, long_leg["token"], exit_day)
        if eod_short is not None and eod_long is not None and eod_short[0] > exit_ts:
            sn, _ = E.leg_cost_rupees(short_leg["entry"], eod_short[1], E.NIFTY_LOT, qty_lots,
                                       slip, exit_day, "SELL")
            ln, _ = E.leg_cost_rupees(long_leg["entry"], eod_long[1], E.NIFTY_LOT, qty_lots,
                                       slip, exit_day, "BUY")
            tr.held_to_dayend_net_rupees = sn + ln
            tr.held_to_dayend_note = f"marked at {eod_short[0].time()} session close"
    return tr


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    args = ap.parse_args(argv)

    con = E.connect()
    idx_bars = E.filter_session(E.load_index_bars(con, start=LOOKBACK_START))
    daily = daily_bars(idx_bars)
    day_lookup = {row[0]: i for i, row in enumerate(daily)}
    trading_days_sorted = [row[0] for row in daily]

    all_expiries_raw = con.execute(
        "SELECT DISTINCT expiry FROM instruments ORDER BY expiry").fetchall()
    all_expiries = [date.fromisoformat(r[0]) for r in all_expiries_raw]
    monthlies = monthly_expiries(all_expiries)

    signals = find_signals(daily)

    trades, skips = [], {}
    for t, support in signals:
        signal_day = daily[t][0]
        if t + 1 >= len(daily):
            skips["no_next_day"] = skips.get("no_next_day", 0) + 1
            continue
        entry_day = daily[t + 1][0]
        if not (E.WINDOW_START_DATE <= entry_day <= E.WINDOW_END_DATE):
            continue

        row = con.execute(
            "SELECT started_at, close FROM market_candles WHERE instrument_token=? "
            "AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=? "
            "ORDER BY started_at LIMIT 1",
            (E.INDEX_TOKEN, f"{entry_day.isoformat()}T09:30:00+05:30",
             f"{entry_day.isoformat()}T09:45:00+05:30")).fetchone()
        if row is None:
            skips["no_entry_bar"] = skips.get("no_entry_bar", 0) + 1
            continue
        entry_at, entry_spot = datetime.fromisoformat(row[0]), float(row[1])

        expiry = E.nearest_expiry_on_or_after(con, entry_day, monthlies)
        if expiry is None:
            skips["no_monthly_expiry"] = skips.get("no_monthly_expiry", 0) + 1
            continue

        short_strike = math.floor(support / STRIKE_STEP) * STRIKE_STEP
        long_strike = short_strike - HEDGE_OFFSET
        chain = option_chain_prices(con, expiry, [short_strike, long_strike], entry_at)
        if chain is None or short_strike not in chain or long_strike not in chain:
            skips["no_strikes"] = skips.get("no_strikes", 0) + 1
            continue
        short_tok, short_prem = chain[short_strike]
        long_tok, long_prem = chain[long_strike]
        credit = short_prem - long_prem
        if credit <= 0:
            skips["non_positive_credit"] = skips.get("non_positive_credit", 0) + 1
            continue

        expiry_idx = day_lookup.get(expiry)
        if expiry_idx is None:
            for i, d in enumerate(trading_days_sorted):
                if d >= expiry:
                    expiry_idx = i
                    break
        if expiry_idx is None or expiry_idx - TIME_EXIT_DAYS_BEFORE_EXPIRY < 0:
            skips["no_time_exit_day"] = skips.get("no_time_exit_day", 0) + 1
            continue
        time_exit_day = trading_days_sorted[expiry_idx - TIME_EXIT_DAYS_BEFORE_EXPIRY]
        if time_exit_day <= entry_day:
            skips["time_exit_before_entry"] = skips.get("time_exit_before_entry", 0) + 1
            continue
        time_exit_at = datetime(time_exit_day.year, time_exit_day.month, time_exit_day.day,
                                 15, 29, tzinfo=E.IST)

        # Daily-close stop-loss signal: spot closes below the long put
        # strike on any daily candle between entry and the time exit ->
        # exit that day at day-end.
        stop_day = None
        for d in trading_days_sorted:
            if d <= entry_day or d > time_exit_day:
                continue
            idx = day_lookup[d]
            if daily[idx][4] < long_strike:
                stop_day = d
                break

        # size_or_floor wants risk PER UNIT (per 1 share/point of premium) --
        # it multiplies by lot_size itself. Passing an already-per-lot value
        # here was a bug (found during the portfolio-sizing follow-up pass):
        # it double-divided by lot_size internally, so it floored to 1 lot
        # far more often than the real 1-2% budget actually required, and
        # silently undersized the rare trade whose true risk was tight
        # enough to afford 2+ lots. Fixed: pass the true per-unit risk.
        max_loss_per_unit = HEDGE_OFFSET - credit
        qty_lots, floored, actual_risk_pct = E.size_or_floor(
            E.CAPITAL, args.risk, max_loss_per_unit, E.NIFTY_LOT)
        risk_per_unit_rupees = max_loss_per_unit * E.NIFTY_LOT  # for the note below, per-LOT display value
        if qty_lots <= 0:
            skips["qty_zero"] = skips.get("qty_zero", 0) + 1
            continue

        effective_time_exit = time_exit_at
        if stop_day is not None:
            stop_exit_at = datetime(stop_day.year, stop_day.month, stop_day.day, 15, 29, tzinfo=E.IST)
            if stop_exit_at < effective_time_exit:
                effective_time_exit = stop_exit_at

        tr = run_spread(con, entry_day, entry_at,
                         {"token": short_tok, "strike": short_strike, "entry": short_prem},
                         {"token": long_tok, "strike": long_strike, "entry": long_prem},
                         credit, effective_time_exit, qty_lots, args.slippage)
        if tr is None:
            skips["no_walk_data"] = skips.get("no_walk_data", 0) + 1
            continue
        if stop_day is not None and tr.reason == "time_exit" and tr.exit_at.date() == stop_day:
            tr.reason = "daily_stop_signal"
        if floored:
            tr.notes += (f" | SIZING_FLOOR: risk-based sizing gave 0 lots "
                         f"(1-lot max loss Rs{risk_per_unit_rupees:,.0f} > "
                         f"{args.risk*100:.0f}% risk budget); floored to 1 lot, "
                         f"actual risk {actual_risk_pct:.1f}% of capital")
        trades.append(tr)

    out, payload = E.save_results(NAME, trades, extra={
        "signals_found": len(signals), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage,
        "params": {"sma_period": SMA_PERIOD, "swing_window": SWING_WINDOW,
                    "support_tolerance": SUPPORT_TOLERANCE, "hedge_offset": HEDGE_OFFSET,
                    "take_profit_frac": TAKE_PROFIT,
                    "time_exit_days_before_expiry": TIME_EXIT_DAYS_BEFORE_EXPIRY},
    })
    m = payload["metrics"]
    print(f"{NAME}: {len(signals)} signals -> {len(trades)} trades (skips: {skips})")
    if m:
        print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
              f"PF {m['profit_factor']} | maxDD {m['max_drawdown_pct_of_peak']}% | "
              f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
