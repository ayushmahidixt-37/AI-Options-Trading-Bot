"""#10 Short Straddle -- 9:20, NIFTY50, community code, spec item #10.

Ported from the community "0920_short_straddle" pattern
(buzzsubash/algo_trading_strategies_india), run every real trading day
against that day's nearest weekly expiry.

Spec implemented:
  Entry     09:20 -- sell ATM Call + ATM Put (ATM = spot LTP rounded to
            nearest 50, `E.atm_strike(spot, 50)`)
  Stop      25% above sell price on EACH leg independently, PLUS a 5-point
            slippage buffer added to the trigger price itself:
            trigger = entry_premium * 1.25 + 5 (this is IN ADDITION to the
            engine's own per-leg cost-model slippage on the fill, not a
            substitute for it)
  Re-entry  if BOTH legs have been stopped out, re-enter a fresh ATM
            straddle at 12:30 (one re-entry only, same day)
  Target    none fixed -- SL-only
  Time      square off at 15:06
  Quantity  via `E.size_or_floor` (the spec's own hardcoded "1 lot, lot
            size 75" is the raw community code's coded default -- replaced
            per the file's explicit instruction to "scale via the
            position-sizing formula instead of copying this raw")

JUDGMENT CALL -- which expiry / which days: the spec names no particular
day-of-week restriction ("Entry: 9:20 AM"), so this runs on EVERY real
trading day in the window, using the nearest weekly expiry on or after
that day (which is the expiry itself on expiry Thursdays, matching how a
"daily 9:20 straddle seller" actually operates in the source community
code this is ported from).

JUDGMENT CALL -- risk-per-unit for sizing: worst case per lot is both legs
independently hitting their own 25%+5pt stop, i.e.
((ce_entry*0.25+5) + (pe_entry*0.25+5)) * NIFTY_LOT, computed once from
the ORIGINAL 09:20 entry premiums and used for the whole day's position
(the file's own convention -- size once at entry, like s07/s08/s09 -- a
mid-day re-entry does not get re-sized).

JUDGMENT CALL -- day filter: the archive pads a flat, constant-price row
across whole Saturdays (and has zero rows on weekday holidays) -- see the
range check in `_is_real_session_day`, reused from s08/s09's finding, to
avoid manufacturing a fake trading day out of that padding.

JUDGMENT CALL -- held-to-dayend counterfactual: since this is a single-day
trade (entry and exit both same calendar day, square-off 15:06), "held to
day-end" means marked at THAT day's real session close (still meaningfully
different from a 25%+5pt stop-out or an early re-entry stop-out). Computed
per leg (across up to 4 legs if a re-entry happened) for any leg that
closed before 15:06, same per-leg approach as s08.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s10_short_straddle_920"
SL_MULT = 0.25
SL_BUFFER = 5.0
ENTRY_TIME = "09:20"
REENTRY_TIME = "12:30"
SQUARE_OFF_TIME = "15:06"
STRIKE_STEP = 50.0
CE_OFFSET = 0.0
PE_OFFSET = 0.0


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


def walk_legs(con, legs, end_at, qty_lots, slip):
    """Walk two legs (CE, PE) independently to their own 25%+5pt stop or
    `end_at`. Returns list of finished-leg dicts with exit info."""
    tokens = [leg["token"] for leg in legs]
    series = leg_series(con, tokens, legs[0]["entry_at"].isoformat(), end_at.isoformat())
    state = {leg["type"]: {"open": True, "exit_ts": None, "exit_prem": None, "reason": None}
              for leg in legs}
    by_type = {leg["type"]: leg for leg in legs}
    triggers = {leg["type"]: leg["entry"] * (1 + SL_MULT) + SL_BUFFER for leg in legs}

    for ts in sorted(series):
        prices = series[ts]
        for otype, leg in by_type.items():
            st = state[otype]
            if not st["open"] or leg["token"] not in prices:
                continue
            p = prices[leg["token"]]
            if p >= triggers[otype]:
                st.update(open=False, exit_ts=ts, exit_prem=p, reason="leg_sl")
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
    net, charges = E.leg_cost_rupees(leg["entry"], exit_prem, E.NIFTY_LOT, qty_lots, slip, exit_day, "SELL")
    return net, charges


def main(argv=None):
    global SL_MULT, SL_BUFFER

    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    ap.add_argument("--sl-pct", type=float, default=25.0,
                     help="per-leg SL as %% rise above own entry premium (default 25)")
    ap.add_argument("--sl-buffer", type=float, default=SL_BUFFER,
                     help="fixed point buffer added to the SL trigger price (default 5)")
    ap.add_argument("--label", default=None,
                     help="if set, save to results/sweeps/<NAME>_<label>.json "
                          "instead of results/<NAME>.json")
    args = ap.parse_args(argv)
    SL_MULT = args.sl_pct / 100.0
    SL_BUFFER = args.sl_buffer

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
        ce = build_leg(con, expiry, entry_at, atm + CE_OFFSET, "CE")
        pe = build_leg(con, expiry, entry_at, atm - PE_OFFSET, "PE")
        if ce is None or pe is None:
            skips["no_legs"] = skips.get("no_legs", 0) + 1
            continue

        # size_or_floor wants risk PER UNIT, not per lot -- fixed during the
        # portfolio-sizing follow-up pass (see engine.py's docstring).
        max_loss_per_unit = ((ce["entry"] * SL_MULT + SL_BUFFER) +
                              (pe["entry"] * SL_MULT + SL_BUFFER))
        qty_lots, floored, actual_risk_pct = E.size_or_floor(
            E.CAPITAL, args.risk, max_loss_per_unit, E.NIFTY_LOT)
        risk_per_unit_rupees = max_loss_per_unit * E.NIFTY_LOT  # per-LOT, for risk_rupees/notes below
        if qty_lots <= 0:
            skips["qty_zero"] = skips.get("qty_zero", 0) + 1
            continue

        square_off_h, square_off_m = int(SQUARE_OFF_TIME[:2]), int(SQUARE_OFF_TIME[3:])
        square_off_at = datetime(day.year, day.month, day.day, square_off_h, square_off_m, tzinfo=E.IST)
        reentry_h, reentry_m = int(REENTRY_TIME[:2]), int(REENTRY_TIME[3:])
        reentry_at_clock = datetime(day.year, day.month, day.day, reentry_h, reentry_m, tzinfo=E.IST)

        # Phase 1: walk to min(re-entry time, square-off) first so we can
        # decide whether a re-entry is triggered; if both legs stop before
        # 12:30 we re-run the remainder from where phase 1 ended.
        phase1_end = min(reentry_at_clock, square_off_at)
        state1 = walk_legs(con, [ce, pe], phase1_end, qty_lots, args.slippage)

        leg_rows = []
        both_stopped_by_reentry = (state1["CE"]["reason"] == "leg_sl" and
                                    state1["PE"]["reason"] == "leg_sl" and
                                    phase1_end == reentry_at_clock)

        final_legs_state = dict(state1)
        reentry_legs = []
        if both_stopped_by_reentry and reentry_at_clock < square_off_at:
            r_entry_at, r_spot = spot_at(con, day, REENTRY_TIME)
            if r_entry_at is not None:
                r_atm = E.atm_strike(r_spot, STRIKE_STEP)
                r_ce = build_leg(con, expiry, r_entry_at, r_atm + CE_OFFSET, "CE")
                r_pe = build_leg(con, expiry, r_entry_at, r_atm - PE_OFFSET, "PE")
                if r_ce is not None and r_pe is not None:
                    reentry_legs = [r_ce, r_pe]
                    state2 = walk_legs(con, [r_ce, r_pe], square_off_at, qty_lots, args.slippage)
                    final_legs_state["CE_r"] = state2["CE"]
                    final_legs_state["PE_r"] = state2["PE"]
        elif phase1_end < square_off_at:
            # Neither/one leg stopped by 12:30: keep walking any still-open
            # original leg(s) to square-off.
            still_open = [leg for leg in (ce, pe) if state1[leg["type"]]["reason"] != "leg_sl"]
            if still_open:
                cont_state = walk_legs(con, still_open, square_off_at, qty_lots, args.slippage)
                for otype in cont_state:
                    final_legs_state[otype] = cont_state[otype]

        total_net, total_charges = 0.0, 0.0
        final_exit_ts = entry_at
        original_pair = [("CE", ce), ("PE", pe)]
        for otype, leg in original_pair:
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
                     direction="short_straddle_920", legs=leg_rows, qty_lots=qty_lots,
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
        "trading_days_considered": len(days), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage,
        "params": {"sl_mult": SL_MULT, "sl_buffer": SL_BUFFER, "entry_time": ENTRY_TIME,
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
