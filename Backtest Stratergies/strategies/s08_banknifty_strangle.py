"""#8 Short Strangle with Hard Stop -- BankNifty Weekly, spec item #8.

DATA LIMITATION (see engine.py module docstring and PROJECT rules): the spec
is written for BANKNIFTY weekly options. This archive's `instruments` table
has exactly one underlying, 'NIFTY' (verified: `SELECT DISTINCT underlying
FROM instruments` returns one row). There is no BankNifty data anywhere in
this archive. This script therefore runs on NIFTY weekly options instead,
using NIFTY's own lot size (E.NIFTY_LOT=50) and volatility -- NOT the
BankNifty numbers this strategy was designed around. Treat these results as
directional-logic validation only, not as what BankNifty would actually have
done. This is also recorded in `extra["data_limitation"]` below.

Spec implemented:
  Entry     Monday of expiry week, 09:20-09:35 (adapted from s07's
            09:30-09:45 monday_entry helper, window shifted 10 minutes
            earlier per this strategy's stated 9:20 entry)
  Structure short call/put at ~0.20 delta OTM each (Black-Scholes, same
            technique as s07), NO hedge legs -- genuinely undefined risk
  Stop      PER-LEG: exit a leg if ITS OWN premium rises 100% above its own
            entry (doubles), independently for CE and PE -- the two legs can
            close at different times. ALSO a combined stop: exit both
            remaining legs if combined MTM loss >= Rs15,000 per lot (i.e.
            threshold_rupees = 15000 * qty_lots for the position actually
            held) -- checked only while BOTH legs are still open, since
            "combined" is meaningless for a single remaining leg.
  Target    exit both legs at 60% of TOTAL premium collected (combined) --
            also only evaluated while both legs are open, for the same
            reason as the combined stop above. Once one leg has been
            stopped individually, the surviving leg runs solo to its own
            per-leg stop or the time exit; there is no longer a "60% of
            total premium collected" figure to aim at since that total no
            longer describes an open two-leg structure.
  Time      force close by 15:00 on expiry day (Thursday, or whatever
            weekday the expiry actually falls on)

JUDGMENT CALL -- risk-per-unit for sizing: the file's own sizing rule says
"for per-leg-SL strategies with no hedge, use the SL trigger's premium
distance x lot_size as risk-per-unit." With two independent legs, the
worst realistic case per lot is bounded by whichever of these is smaller:
(a) both legs actually double before either check fires: (ce_entry +
pe_entry) * NIFTY_LOT, or (b) the explicit combined-stop cap the strategy
itself states, Rs15,000/lot. risk_per_unit_rupees = min(a, b), computed from
the real entry premiums of that week's structure.

JUDGMENT CALL -- data padding: the raw table carries a flat, padded row
across every calendar minute, including non-trading Saturdays and (all-day)
weekday holidays being simply absent rather than padded intraday -- checked
directly (2022-01-08 and 2022-01-22, both Saturdays, show a single constant
close across the whole would-be session; 2022-01-26, a Wednesday holiday,
has zero rows at all). Only Saturdays are a live risk for "Monday of expiry
week" entry logic if a Monday itself were a holiday and the naive query still
finds a padded row; guarded here by rejecting any entry bar whose day shows
zero index range over the session (see `_is_real_session_day`).

JUDGMENT CALL -- held-to-day-end counterfactual for a two-leg, potentially
multi-day trade: unlike s07 (whose 4 legs always exit together, on one
timestamp), this structure's two legs can close on DIFFERENT days (e.g. CE
stops Monday, PE runs to the Thursday time exit). The single per-trade
`held_to_dayend_*` fields on `Trade` can only describe one counterfactual, so
per LEG that exited before the Thursday time exit, this script substitutes
that leg's own day-end mark (on ITS OWN exit day) for its actual net, sums
that with the actual net of any leg that ran to the time exit, and reports
the delta on the trade as a whole. This is a deliberate deviation from s07's
whole-structure/single-day pattern, justified because legs here genuinely
can close on different days.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s08_banknifty_strangle"
TARGET_DELTA = 0.20
TAKE_PROFIT = 0.60          # of total premium collected
COMBINED_STOP_PER_LOT = 15_000.0
LEG_STOP_MULT = 2.0         # premium doubles = 100% rise


def _is_real_session_day(con, day: date) -> bool:
    """Reject Saturdays / padded non-trading days: the archive carries a
    flat, constant-price row across the whole would-be session on those
    days (checked directly), so a nonzero session high-low range is a
    reliable real-trading-day test."""
    row = con.execute(
        "SELECT MAX(high)-MIN(low) FROM market_candles WHERE instrument_token=? "
        "AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=?",
        (E.INDEX_TOKEN, f"{day.isoformat()}T09:15:00+05:30",
         f"{day.isoformat()}T15:30:00+05:30")).fetchone()
    return row is not None and row[0] is not None and row[0] > 1e-6


def monday_entry_920(con, expiry):
    for back in (3, 2, 1):
        day = expiry - timedelta(days=back)
        if not _is_real_session_day(con, day):
            continue
        row = con.execute(
            "SELECT started_at, close FROM market_candles WHERE instrument_token=? "
            "AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=? "
            "ORDER BY started_at LIMIT 1",
            (E.INDEX_TOKEN, f"{day.isoformat()}T09:20:00+05:30",
             f"{day.isoformat()}T09:35:00+05:30")).fetchone()
        if row:
            return datetime.fromisoformat(row[0]), float(row[1])
    return None, None


def chain_at(con, expiry, ts):
    instruments = con.execute(
        "SELECT token, strike, option_type FROM instruments WHERE expiry=?",
        (expiry.isoformat(),)).fetchall()
    if not instruments:
        return None
    marks = ",".join("?" * len(instruments))
    priced = con.execute(
        f"SELECT instrument_token, close, implied_volatility FROM market_candles "
        f"WHERE instrument_token IN ({marks}) AND timeframe='ONE_MINUTE' AND started_at=?",
        [i[0] for i in instruments] + [ts.isoformat()]).fetchall()
    if not priced:
        return None
    meta = {t: (k, ot) for t, k, ot in instruments}
    return meta, {r[0]: (float(r[1]), r[2]) for r in priced}


def build_strangle(con, expiry, entry_at, spot, meta, priced):
    yrs = E.years_to_expiry(expiry, entry_at)
    chain = {}
    for token, (ltp, iv) in priced.items():
        strike, otype = meta[token]
        d = E.bs_delta(spot, strike, yrs, iv, otype)
        if d is not None:
            chain[(otype, strike)] = {"token": token, "ltp": ltp, "delta": d, "iv": iv}
    calls = [(k, v) for (ot, k), v in chain.items() if ot == "CE"]
    puts = [(k, v) for (ot, k), v in chain.items() if ot == "PE"]
    if not calls or not puts:
        return None
    sc_k, sc = min(calls, key=lambda kv: abs(kv[1]["delta"] - TARGET_DELTA))
    sp_k, sp = min(puts, key=lambda kv: abs(kv[1]["delta"] + TARGET_DELTA))
    return {
        "CE": {"strike": sc_k, "token": sc["token"], "entry": sc["ltp"]},
        "PE": {"strike": sp_k, "token": sp["token"], "entry": sp["ltp"]},
    }


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


def run_strangle(con, day, expiry, entry_at, legs, qty_lots, slip):
    time_exit = datetime(expiry.year, expiry.month, expiry.day, 15, 0, tzinfo=E.IST)
    ce, pe = legs["CE"], legs["PE"]
    tokens = [ce["token"], pe["token"]]
    series = leg_series(con, tokens, entry_at.isoformat(), time_exit.isoformat())

    state = {
        "CE": {"open": True, "exit_ts": None, "exit_prem": None, "reason": None},
        "PE": {"open": True, "exit_ts": None, "exit_prem": None, "reason": None},
    }
    total_credit = ce["entry"] + pe["entry"]

    def close_leg(side, ts, prem, reason):
        state[side]["open"] = False
        state[side]["exit_ts"] = ts
        state[side]["exit_prem"] = prem
        state[side]["reason"] = reason

    for ts in sorted(series):
        prices = series[ts]
        if state["CE"]["open"] and ce["token"] in prices:
            p = prices[ce["token"]]
            if p >= LEG_STOP_MULT * ce["entry"]:
                close_leg("CE", ts, p, "leg_stop")
        if state["PE"]["open"] and pe["token"] in prices:
            p = prices[pe["token"]]
            if p >= LEG_STOP_MULT * pe["entry"]:
                close_leg("PE", ts, p, "leg_stop")

        if state["CE"]["open"] and state["PE"]["open"] and ce["token"] in prices and pe["token"] in prices:
            cur_ce, cur_pe = prices[ce["token"]], prices[pe["token"]]
            cur_value = cur_ce + cur_pe
            combined_loss = (cur_value - total_credit) * E.NIFTY_LOT * qty_lots
            if combined_loss >= COMBINED_STOP_PER_LOT * qty_lots:
                close_leg("CE", ts, cur_ce, "combined_stop")
                close_leg("PE", ts, cur_pe, "combined_stop")
            else:
                profit = total_credit - cur_value
                if profit >= TAKE_PROFIT * total_credit:
                    close_leg("CE", ts, cur_ce, "combined_target")
                    close_leg("PE", ts, cur_pe, "combined_target")

        if not state["CE"]["open"] and not state["PE"]["open"]:
            break

    # Force-close whatever is still open at the time exit.
    for side, leg in (("CE", ce), ("PE", pe)):
        if state[side]["open"]:
            eod = E.price_at_or_before(con, leg["token"], time_exit.isoformat())
            if eod is None:
                return None
            close_leg(side, eod[0].isoformat(), eod[1], "time_exit")

    total_net, total_charges = 0.0, 0.0
    leg_rows = []
    final_exit_ts = None
    for side, leg in (("CE", ce), ("PE", pe)):
        st = state[side]
        exit_ts = datetime.fromisoformat(st["exit_ts"]) if isinstance(st["exit_ts"], str) else st["exit_ts"]
        exit_day = exit_ts.date()
        net, charges = E.leg_cost_rupees(leg["entry"], st["exit_prem"], E.NIFTY_LOT, qty_lots,
                                          slip, exit_day, "SELL")
        total_net += net
        total_charges += charges
        leg_rows.append({"type": side, "strike": leg["strike"], "action": "SELL",
                          "entry_premium": leg["entry"], "exit_premium": st["exit_prem"],
                          "net_rupees": round(net, 2), "reason": st["reason"], "exit_at": exit_ts.isoformat()})
        if final_exit_ts is None or exit_ts > final_exit_ts:
            final_exit_ts = exit_ts

    tr = E.Trade(strategy=NAME, day=day, entry_at=entry_at, exit_at=final_exit_ts,
                 direction="short_strangle", legs=leg_rows, qty_lots=qty_lots,
                 lot_size=E.NIFTY_LOT, reason="+".join(sorted({r["reason"] for r in leg_rows})),
                 gross_rupees=total_net + total_charges, net_rupees=total_net,
                 charges_rupees=total_charges,
                 risk_rupees=min(COMBINED_STOP_PER_LOT, total_credit * E.NIFTY_LOT) * qty_lots,
                 notes=f"expiry={expiry} ce_strike={ce['strike']} pe_strike={pe['strike']} "
                       f"credit={total_credit:.2f}")

    # Per-leg held-to-dayend counterfactual for any leg that exited before
    # the Thursday time exit (see module docstring for why this is per-leg,
    # not whole-structure like s07).
    any_early = any(r["reason"] != "time_exit" for r in leg_rows)
    if any_early:
        counter_total = 0.0
        notes = []
        for side, leg in (("CE", ce), ("PE", pe)):
            st = state[side]
            if st["reason"] == "time_exit":
                counter_total += next(r["net_rupees"] for r in leg_rows if r["type"] == side)
                continue
            exit_ts = datetime.fromisoformat(st["exit_ts"]) if isinstance(st["exit_ts"], str) else st["exit_ts"]
            eod = E.day_end_price(con, leg["token"], exit_ts.date())
            if eod is None:
                counter_total += next(r["net_rupees"] for r in leg_rows if r["type"] == side)
                continue
            eod_ts, eod_prem = eod
            eod_net, _ = E.leg_cost_rupees(leg["entry"], eod_prem, E.NIFTY_LOT, qty_lots,
                                            slip, exit_ts.date(), "SELL")
            counter_total += eod_net
            notes.append(f"{side} marked at {eod_ts.time()} close {eod_prem}")
        tr.held_to_dayend_net_rupees = counter_total
        tr.held_to_dayend_note = "; ".join(notes) if notes else "all legs ran to time exit"
    return tr


def main(argv=None):
    global LEG_STOP_MULT, COMBINED_STOP_PER_LOT, TARGET_DELTA

    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    ap.add_argument("--leg-stop-pct", type=float, default=100.0,
                     help="per-leg SL as %% rise above own entry premium "
                          "(default 100 = doubles, matches original spec)")
    ap.add_argument("--combined-stop-per-lot", type=float, default=COMBINED_STOP_PER_LOT)
    ap.add_argument("--target-delta", type=float, default=TARGET_DELTA,
                     help="short-strike |delta| target (default 0.20, matches spec)")
    ap.add_argument("--label", default=None,
                     help="if set, save to results/sweeps/<NAME>_<label>.json "
                          "instead of results/<NAME>.json")
    args = ap.parse_args(argv)
    LEG_STOP_MULT = 1.0 + args.leg_stop_pct / 100.0
    COMBINED_STOP_PER_LOT = args.combined_stop_per_lot
    TARGET_DELTA = args.target_delta

    con = E.connect()
    expiries = E.list_expiries(con)
    trades, skips = [], {}
    n_floored = 0
    for expiry in expiries:
        entry_at, spot = monday_entry_920(con, expiry)
        if entry_at is None:
            skips["no_entry_bar"] = skips.get("no_entry_bar", 0) + 1
            continue
        chain = chain_at(con, expiry, entry_at)
        if chain is None:
            skips["chain_unpriced"] = skips.get("chain_unpriced", 0) + 1
            continue
        legs = build_strangle(con, expiry, entry_at, spot, *chain)
        if legs is None:
            skips["no_strangle"] = skips.get("no_strangle", 0) + 1
            continue
        total_credit = legs["CE"]["entry"] + legs["PE"]["entry"]
        # size_or_floor wants risk PER UNIT -- fixed during the portfolio-
        # sizing follow-up pass (see engine.py's docstring). Both terms here
        # were already per-lot (COMBINED_STOP_PER_LOT is a per-lot rupee
        # constant, total_credit*NIFTY_LOT converts credit to per-lot), so
        # convert the min back down to per-unit before calling.
        max_loss_per_unit = min(COMBINED_STOP_PER_LOT / E.NIFTY_LOT, total_credit)
        qty_lots, floored, actual_risk_pct = E.size_or_floor(
            E.CAPITAL, args.risk, max_loss_per_unit, E.NIFTY_LOT)
        risk_per_unit_rupees = max_loss_per_unit * E.NIFTY_LOT  # per-LOT, for the note below
        if qty_lots <= 0:
            skips["qty_zero"] = skips.get("qty_zero", 0) + 1
            continue
        tr = run_strangle(con, entry_at.date(), expiry, entry_at, legs, qty_lots, args.slippage)
        if tr is None:
            skips["no_walk_data"] = skips.get("no_walk_data", 0) + 1
            continue
        if floored:
            n_floored += 1
            tr.notes += (f" | SIZING_FLOOR: risk-based sizing gave 0 lots "
                         f"(1-lot risk Rs{risk_per_unit_rupees:,.0f} > "
                         f"{args.risk*100:.0f}% risk budget); floored to 1 lot, "
                         f"actual risk {actual_risk_pct:.1f}% of capital")
        trades.append(tr)

    floored_pct = round(100 * n_floored / len(trades), 1) if trades else None
    save_name = f"sweeps/{NAME}_{args.label}" if args.label else NAME
    out, payload = E.save_results(save_name, trades, extra={
        "data_limitation": (
            "Spec is BANKNIFTY; this archive has NIFTY options only (verified: "
            "SELECT DISTINCT underlying FROM instruments returns exactly one "
            "row). Run on NIFTY as a substitute underlying with NIFTY's own lot "
            "size and volatility -- NOT the BankNifty numbers this strategy was "
            "designed around. Treat these results as directional-logic "
            "validation only, not as what BankNifty would actually have done."),
        "expiries_considered": len(expiries), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage,
        "params": {"target_delta": TARGET_DELTA, "take_profit_frac": TAKE_PROFIT,
                    "leg_stop_mult": LEG_STOP_MULT, "combined_stop_per_lot": COMBINED_STOP_PER_LOT},
        "floored_trades": n_floored, "floored_pct_of_trades": floored_pct,
    })
    m = payload["metrics"]
    print(f"{NAME}: {len(expiries)} expiries -> {len(trades)} cycles (skips: {skips})")
    print(f"  floored to 1 lot: {n_floored}/{len(trades)} ({floored_pct}%)")
    if m:
        print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
              f"PF {m['profit_factor']} | maxDD {m['max_drawdown_pct_of_peak']}% | "
              f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
