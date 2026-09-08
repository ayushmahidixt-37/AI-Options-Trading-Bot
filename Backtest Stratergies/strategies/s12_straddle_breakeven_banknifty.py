"""#12 Short Straddle with Breakeven Adjustment -- BankNifty, community code,
spec item #12 -- RUN ON NIFTY, see data-limitation flag below.

DATA LIMITATION (see engine.py module docstring and PROJECT rules): the spec
is written for BANKNIFTY weekly options. This archive's `instruments` table
has exactly one underlying, 'NIFTY' (verified: `SELECT DISTINCT underlying
FROM instruments` returns one row). There is no BankNifty data anywhere in
this archive. This script therefore runs on NIFTY weekly options instead,
using NIFTY's own lot size (E.NIFTY_LOT=50) and volatility -- NOT the
BankNifty numbers this strategy was designed around. Treat these results as
directional-logic validation only, not as what BankNifty would actually have
done. Also recorded in `extra["data_limitation"]` below.

Ported from the community "short_straddle/trailing_stop_loss"-style pattern
(buzzsubash/algo_trading_strategies_india), run every real trading day
against that day's nearest weekly expiry.

Spec implemented:
  Entry     09:59 -- sell ATM Call + ATM Put (ATM = spot rounded to nearest
            100 for THIS strategy specifically, `E.atm_strike(spot, 100)` --
            note this differs from s10/s11's nearest-50)
  Stop      ASYMMETRIC, independent per leg: Call SL at 20% above its sell
            price, Put SL at 23% above its sell price (no +5pt buffer --
            unlike s10/s11, the spec for #12 does not mention one, so none
            is added here)
  Adjustment THE NOTABLE TECHNIQUE: the instant either leg's SL is hit, the
            SURVIVING leg's stop is immediately moved to ITS OWN breakeven
            (entry) price -- "can't lose further on this leg" -- and it
            keeps running for further profit, capped at zero additional
            loss from that point on.
  Time      square off at 15:06

JUDGMENT CALL (spec explicitly flags this as ambiguous) -- the 12:30
re-entry: "a second adjustment/re-entry check happens at 12:30" with no
further detail. This script's reading, following s10/s11's sibling pattern:
if BOTH legs are closed (one by its original asymmetric SL, the other by
its breakeven-adjusted SL) by 12:30, AND there is still session time left,
re-enter a fresh ATM(100) straddle at 12:30 with the SAME asymmetric-SL +
breakeven-adjustment rules applied to the new legs, run to the 15:06
square-off, no second re-entry. If fewer than both legs are closed by
12:30, there is nothing to re-enter -- the surviving leg (at its original
or breakeven-adjusted stop) simply keeps running to 15:06.

JUDGMENT CALL -- risk-per-unit for sizing: once the breakeven adjustment
fires, the surviving leg is capped near zero further loss (its stop sits at
its own entry price), so the structure's real worst-case loss per lot is
effectively bounded by whichever leg's ORIGINAL stop triggers first --
max(ce_entry*0.20, pe_entry*0.23) * NIFTY_LOT -- rather than the sum of both
legs' distances (which would double-count the leg that gets capped at
breakeven the moment the other one trips).

JUDGMENT CALL -- day filter: the archive pads a flat, constant-price row
across whole Saturdays (and has zero rows on weekday holidays) -- reused
range check from s08/s09/s10's finding, to avoid a fake trading day out of
that padding.

JUDGMENT CALL -- held-to-dayend counterfactual: single-day trade (entry and
exit both same calendar day, square-off 15:06); "held to day-end" means
marked at that day's real session close, computed per leg (across up to 4
legs if a re-entry happened) for any leg that closed before 15:06 -- same
per-leg approach as s08/s10/s11.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s12_straddle_breakeven_banknifty"
CE_SL_MULT = 0.20
PE_SL_MULT = 0.23
ENTRY_TIME = "09:59"
REENTRY_TIME = "12:30"
SQUARE_OFF_TIME = "15:06"
STRIKE_STEP = 100.0


def _is_real_session_day(con, day: date) -> bool:
    row = con.execute(
        "SELECT MAX(high)-MIN(low) FROM market_candles WHERE instrument_token=? "
        "AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=?",
        (E.INDEX_TOKEN, f"{day.isoformat()}T09:15:00+05:30",
         f"{day.isoformat()}T15:30:00+05:30")).fetchone()
    return row is not None and row[0] is not None and row[0] > 1e-6


def real_trading_days(con, bars):
    return [d for d, lo, hi in E.day_index(bars) if _is_real_session_day(con, d)]


def spot_at(con, day, hhmm, window_min=15):
    h, m = int(hhmm[:2]), int(hhmm[3:])
    start = datetime(day.year, day.month, day.day, h, m, tzinfo=E.IST)
    end = start + timedelta(minutes=window_min)
    row = con.execute(
        "SELECT started_at, close FROM market_candles WHERE instrument_token=? "
        "AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=? "
        "ORDER BY started_at LIMIT 1",
        (E.INDEX_TOKEN, start.isoformat(), end.isoformat())).fetchone()
    if row is None:
        return None, None
    return datetime.fromisoformat(row[0]), float(row[1])


def build_leg(con, expiry, entry_at, strike, otype):
    tok = E.option_token_at(con, expiry, strike, otype)
    if tok is None:
        return None
    bar = E.price_at_or_after(con, tok, entry_at.isoformat())
    if bar is None or bar[0].date() != entry_at.date():
        return None
    return {"type": otype, "strike": strike, "token": tok, "entry": bar[1], "entry_at": bar[0]}


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


SL_MULT = {"CE": CE_SL_MULT, "PE": PE_SL_MULT}


def walk_legs_breakeven(con, legs, end_at):
    """Walk CE+PE independently to their own asymmetric SL. The instant one
    leg's SL fires, the other's trigger is moved to ITS OWN entry price
    (breakeven) for the rest of the walk."""
    tokens = [leg["token"] for leg in legs]
    series = leg_series(con, tokens, legs[0]["entry_at"].isoformat(), end_at.isoformat())
    by_type = {leg["type"]: leg for leg in legs}
    state = {t: {"open": True, "exit_ts": None, "exit_prem": None, "reason": None,
                  "breakeven": False} for t in by_type}
    triggers = {t: leg["entry"] * (1 + SL_MULT[t]) for t, leg in by_type.items()}

    for ts in sorted(series):
        prices = series[ts]
        fired_this_tick = []
        for otype, leg in by_type.items():
            st = state[otype]
            if not st["open"] or leg["token"] not in prices:
                continue
            p = prices[leg["token"]]
            if p >= triggers[otype]:
                reason = "breakeven_stop" if st["breakeven"] else "leg_sl"
                st.update(open=False, exit_ts=ts, exit_prem=p, reason=reason)
                fired_this_tick.append(otype)
        for otype in fired_this_tick:
            other = "PE" if otype == "CE" else "CE"
            ost = state[other]
            if ost["open"] and not ost["breakeven"]:
                ost["breakeven"] = True
                triggers[other] = by_type[other]["entry"]
        if not any(s["open"] for s in state.values()):
            break

    for otype, leg in by_type.items():
        st = state[otype]
        if st["open"]:
            eod = E.price_at_or_before(con, leg["token"], end_at.isoformat())
            if eod is not None:
                st.update(open=False, exit_ts=eod[0].isoformat(), exit_prem=eod[1], reason="time_exit")
            else:
                st.update(open=False, exit_ts=leg["entry_at"].isoformat(), exit_prem=leg["entry"],
                           reason="no_data")
    return state


def leg_net(leg, exit_prem, exit_day, qty_lots, slip):
    return E.leg_cost_rupees(leg["entry"], exit_prem, E.NIFTY_LOT, qty_lots, slip, exit_day, "SELL")


def main(argv=None):
    global CE_SL_MULT, PE_SL_MULT, SL_MULT

    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    ap.add_argument("--ce-sl-pct", type=float, default=CE_SL_MULT * 100,
                     help="Call leg SL as %% rise above own entry premium (default 20)")
    ap.add_argument("--pe-sl-pct", type=float, default=PE_SL_MULT * 100,
                     help="Put leg SL as %% rise above own entry premium (default 23)")
    ap.add_argument("--label", default=None,
                     help="if set, save to results/sweeps/<NAME>_<label>.json "
                          "instead of results/<NAME>.json")
    args = ap.parse_args(argv)
    CE_SL_MULT = args.ce_sl_pct / 100.0
    PE_SL_MULT = args.pe_sl_pct / 100.0
    SL_MULT = {"CE": CE_SL_MULT, "PE": PE_SL_MULT}

    con = E.connect()
    idx_bars = E.filter_session(E.load_index_bars(con))
    days = real_trading_days(con, idx_bars)
    all_expiries = E.list_expiries(con)

    trades, skips = [], {}
    n_floored = 0
    for day in days:
        if not (E.WINDOW_START_DATE <= day <= E.WINDOW_END_DATE):
            continue
        entry_at, spot = spot_at(con, day, ENTRY_TIME)
        if entry_at is None:
            skips["no_entry_bar"] = skips.get("no_entry_bar", 0) + 1
            continue
        expiry = E.nearest_expiry_on_or_after(con, day, all_expiries)
        if expiry is None:
            skips["no_expiry"] = skips.get("no_expiry", 0) + 1
            continue

        atm = E.atm_strike(spot, STRIKE_STEP)
        ce = build_leg(con, expiry, entry_at, atm, "CE")
        pe = build_leg(con, expiry, entry_at, atm, "PE")
        if ce is None or pe is None:
            skips["no_legs"] = skips.get("no_legs", 0) + 1
            continue

        # size_or_floor wants risk PER UNIT, not per lot -- fixed during the
        # portfolio-sizing follow-up pass (see engine.py's docstring).
        max_loss_per_unit = max(ce["entry"] * CE_SL_MULT, pe["entry"] * PE_SL_MULT)
        qty_lots, floored, actual_risk_pct = E.size_or_floor(
            E.CAPITAL, args.risk, max_loss_per_unit, E.NIFTY_LOT)
        risk_per_unit_rupees = max_loss_per_unit * E.NIFTY_LOT  # per-LOT, for risk_rupees/notes below
        if qty_lots <= 0:
            skips["qty_zero"] = skips.get("qty_zero", 0) + 1
            continue

        so_h, so_m = int(SQUARE_OFF_TIME[:2]), int(SQUARE_OFF_TIME[3:])
        square_off_at = datetime(day.year, day.month, day.day, so_h, so_m, tzinfo=E.IST)
        re_h, re_m = int(REENTRY_TIME[:2]), int(REENTRY_TIME[3:])
        reentry_at_clock = datetime(day.year, day.month, day.day, re_h, re_m, tzinfo=E.IST)

        phase1_end = min(reentry_at_clock, square_off_at)
        state1 = walk_legs_breakeven(con, [ce, pe], phase1_end)

        final_legs_state = dict(state1)
        reentry_legs = []
        stopped_reasons = ("leg_sl", "breakeven_stop")
        both_closed_by_reentry = (state1["CE"]["reason"] in stopped_reasons and
                                   state1["PE"]["reason"] in stopped_reasons and
                                   phase1_end == reentry_at_clock)
        if both_closed_by_reentry and reentry_at_clock < square_off_at:
            r_entry_at, r_spot = spot_at(con, day, REENTRY_TIME)
            if r_entry_at is not None:
                r_atm = E.atm_strike(r_spot, STRIKE_STEP)
                r_ce = build_leg(con, expiry, r_entry_at, r_atm, "CE")
                r_pe = build_leg(con, expiry, r_entry_at, r_atm, "PE")
                if r_ce is not None and r_pe is not None:
                    reentry_legs = [r_ce, r_pe]
                    state2 = walk_legs_breakeven(con, [r_ce, r_pe], square_off_at)
                    final_legs_state["CE_r"] = state2["CE"]
                    final_legs_state["PE_r"] = state2["PE"]
        elif phase1_end < square_off_at:
            still_open = [leg for leg in (ce, pe) if state1[leg["type"]]["reason"] not in stopped_reasons]
            if still_open:
                # Re-walk BOTH legs from the original entry through to
                # square-off (deterministic, so the already-closed leg
                # reproduces the same phase-1 closure) so the still-open
                # leg's breakeven adjustment -- triggered by the OTHER
                # leg's closure -- is correctly re-derived; only the still-
                # open leg's resulting state is actually used below.
                cont_state = walk_legs_breakeven(con, [ce, pe], square_off_at)
                for otype in ("CE", "PE"):
                    if state1[otype]["reason"] not in stopped_reasons:
                        final_legs_state[otype] = cont_state[otype]

        total_net, total_charges = 0.0, 0.0
        final_exit_ts = entry_at
        leg_rows = []
        for otype, leg in (("CE", ce), ("PE", pe)):
            st = final_legs_state.get(otype)
            if st is None:
                continue
            exit_ts = datetime.fromisoformat(st["exit_ts"]) if isinstance(st["exit_ts"], str) else st["exit_ts"]
            net, charges = leg_net(leg, st["exit_prem"], exit_ts.date(), qty_lots, args.slippage)
            total_net += net
            total_charges += charges
            leg_rows.append({"type": otype, "strike": leg["strike"], "action": "SELL",
                              "entry_premium": leg["entry"], "exit_premium": st["exit_prem"],
                              "net_rupees": round(net, 2), "reason": st["reason"],
                              "phase": "original", "exit_at": exit_ts.isoformat()})
            final_exit_ts = max(final_exit_ts, exit_ts)

        if reentry_legs:
            for otype, leg in (("CE", reentry_legs[0]), ("PE", reentry_legs[1])):
                st = final_legs_state.get(f"{otype}_r")
                if st is None:
                    continue
                exit_ts = datetime.fromisoformat(st["exit_ts"]) if isinstance(st["exit_ts"], str) else st["exit_ts"]
                net, charges = leg_net(leg, st["exit_prem"], exit_ts.date(), qty_lots, args.slippage)
                total_net += net
                total_charges += charges
                leg_rows.append({"type": otype, "strike": leg["strike"], "action": "SELL",
                                  "entry_premium": leg["entry"], "exit_premium": st["exit_prem"],
                                  "net_rupees": round(net, 2), "reason": st["reason"],
                                  "phase": "reentry", "exit_at": exit_ts.isoformat()})
                final_exit_ts = max(final_exit_ts, exit_ts)

        reasons = sorted({r["reason"] for r in leg_rows})
        tr = E.Trade(strategy=NAME, day=day, entry_at=entry_at, exit_at=final_exit_ts,
                     direction="short_straddle_breakeven", legs=leg_rows, qty_lots=qty_lots,
                     lot_size=E.NIFTY_LOT, reason="+".join(reasons),
                     gross_rupees=total_net + total_charges, net_rupees=total_net,
                     charges_rupees=total_charges, risk_rupees=risk_per_unit_rupees * qty_lots,
                     notes=f"expiry={expiry} atm={atm} reentry={'yes' if reentry_legs else 'no'}")

        any_early = any(r["reason"] != "time_exit" for r in leg_rows)
        if any_early:
            counter_total = 0.0
            notes = []
            for r in leg_rows:
                if r["reason"] == "time_exit":
                    counter_total += r["net_rupees"]
                    continue
                leg_obj = ce if (r["phase"] == "original" and r["type"] == "CE") else \
                          pe if (r["phase"] == "original" and r["type"] == "PE") else \
                          (reentry_legs[0] if r["type"] == "CE" else reentry_legs[1])
                eod = E.day_end_price(con, leg_obj["token"], day)
                if eod is None:
                    counter_total += r["net_rupees"]
                    continue
                eod_ts, eod_prem = eod
                eod_net, _ = leg_net(leg_obj, eod_prem, day, qty_lots, args.slippage)
                counter_total += eod_net
                notes.append(f"{r['phase']}-{r['type']} marked at {eod_ts.time()} close {eod_prem}")
            tr.held_to_dayend_net_rupees = counter_total
            tr.held_to_dayend_note = "; ".join(notes) if notes else "all legs ran to time exit"

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
        "trading_days_considered": len(days), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage,
        "params": {"ce_sl_mult": CE_SL_MULT, "pe_sl_mult": PE_SL_MULT, "entry_time": ENTRY_TIME,
                    "reentry_time": REENTRY_TIME, "square_off_time": SQUARE_OFF_TIME,
                    "strike_step": STRIKE_STEP},
        "floored_trades": n_floored, "floored_pct_of_trades": floored_pct,
    })
    m = payload["metrics"]
    print(f"{NAME}: {len(days)} trading days -> {len(trades)} trades (skips: {skips})")
    print(f"  floored to 1 lot: {n_floored}/{len(trades)} ({floored_pct}%)")
    if m:
        print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
              f"PF {m['profit_factor']} | maxDD {m['max_drawdown_pct_of_peak']}% | "
              f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
