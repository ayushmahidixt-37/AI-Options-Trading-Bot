"""#7 Iron Condor -- NIFTY weekly expiry, 2022-2023.

Entry Monday of expiry week (or the next trading day if Monday is a
holiday), 09:30-09:45. Short call/put at ~0.15 delta OTM; long hedges 200
points further OTM. Exit whole structure if combined MTM loss >= 1.5x net
credit, or at 70% of net credit captured, or force-close by 15:00 on expiry
day, whichever comes first.

Delta is not stored -- computed Black-Scholes from the stored IV (percent),
same technique as clean_room/backtest_iron_condor.py. Position sizing uses
the structure's real defined max loss (wing width - net credit) as the
risk-per-unit in the file's sizing formula, since that IS the honest
worst-case risk of a credit spread (unlike the single-leg strategies, no
delta-based approximation is needed here).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s07_iron_condor"
TARGET_DELTA = 0.15
WING_OFFSET = 200.0
TAKE_PROFIT = 0.70
STOP_MULT = 1.5


def monday_entry(con, expiry):
    for back in (3, 2, 1):
        day = expiry - timedelta(days=back)
        row = con.execute(
            "SELECT started_at, close FROM market_candles WHERE instrument_token=? "
            "AND timeframe='ONE_MINUTE' AND started_at>=? AND started_at<=? "
            "ORDER BY started_at LIMIT 1",
            (E.INDEX_TOKEN, f"{day.isoformat()}T09:30:00+05:30",
             f"{day.isoformat()}T09:45:00+05:30")).fetchone()
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


def build_condor(con, expiry, entry_at, spot, meta, priced, wing_mode="fixed",
                  wing_offset=WING_OFFSET, sw_multiplier=0.5, strike_step=50.0):
    """wing_mode='fixed': long legs sit exactly `wing_offset` points beyond
    the short strikes (the original 200pt spec). wing_mode='straddle-width':
    long legs sit `combined ATM straddle premium x sw_multiplier` points
    beyond the short strikes instead (rounded to the nearest strike step) --
    an IV-adaptive wing width, per AlgoTest's "Straddle Width Multiplier"
    method, meant to fix both the "no 200pt wing priced" skip and the
    sizing-floor problem (narrower wings in calm IV -> smaller, more
    consistent max-loss-per-lot)."""
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

    if wing_mode == "straddle-width":
        atm_k = E.atm_strike(spot, strike_step)
        atm_ce = chain.get(("CE", atm_k))
        atm_pe = chain.get(("PE", atm_k))
        if atm_ce is None or atm_pe is None:
            return None
        atm_straddle_premium = atm_ce["ltp"] + atm_pe["ltp"]
        raw = atm_straddle_premium * sw_multiplier
        eff_offset = max(strike_step, round(raw / strike_step) * strike_step)
    else:
        eff_offset = wing_offset

    lc = chain.get(("CE", sc_k + eff_offset))
    lp = chain.get(("PE", sp_k - eff_offset))
    if lc is None or lp is None:
        return None
    credit = (sc["ltp"] + sp["ltp"]) - (lc["ltp"] + lp["ltp"])
    if credit <= 0:
        return None
    legs = [
        {"type": "CE", "strike": sc_k, "action": "SELL", "token": sc["token"], "entry": sc["ltp"]},
        {"type": "PE", "strike": sp_k, "action": "SELL", "token": sp["token"], "entry": sp["ltp"]},
        {"type": "CE", "strike": sc_k + eff_offset, "action": "BUY", "token": lc["token"], "entry": lc["ltp"]},
        {"type": "PE", "strike": sp_k - eff_offset, "action": "BUY", "token": lp["token"], "entry": lp["ltp"]},
    ]
    max_loss_per_unit = eff_offset - credit
    return legs, credit, max_loss_per_unit


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


def structure_value(legs, prices):
    val = 0.0
    for leg in legs:
        p = prices.get(leg["token"])
        if p is None:
            return None
        val += p if leg["action"] == "SELL" else -p
    return val


def run_condor(con, day, expiry, entry_at, legs, credit, qty_lots, slip,
               take_profit=TAKE_PROFIT):
    time_exit = datetime(expiry.year, expiry.month, expiry.day, 15, 0, tzinfo=E.IST)
    tokens = [leg["token"] for leg in legs]
    series = leg_series(con, tokens, entry_at.isoformat(), time_exit.isoformat())
    last_ts, last_prices, reason = None, None, "time"
    for ts in sorted(series):
        prices = series[ts]
        if len(prices) < len(legs):
            continue
        value = structure_value(legs, prices)
        if value is None:
            continue
        last_ts, last_prices = ts, prices
        profit = credit - value
        if profit >= take_profit * credit:
            reason = "target"
            break
        if profit <= -STOP_MULT * credit:
            reason = "stop"
            break
    else:
        pass
    if last_ts is None:
        return None
    exit_ts = datetime.fromisoformat(last_ts)
    exit_day = exit_ts.date()

    total_net, total_charges = 0.0, 0.0
    leg_rows = []
    for leg in legs:
        exit_prem = last_prices[leg["token"]]
        net, charges = E.leg_cost_rupees(leg["entry"], exit_prem, E.NIFTY_LOT, qty_lots,
                                          slip, exit_day, leg["action"])
        total_net += net
        total_charges += charges
        leg_rows.append({"type": leg["type"], "strike": leg["strike"], "action": leg["action"],
                          "entry_premium": leg["entry"], "exit_premium": exit_prem,
                          "net_rupees": round(net, 2)})

    tr = E.Trade(strategy=NAME, day=day, entry_at=entry_at, exit_at=exit_ts,
                 direction="iron_condor", legs=leg_rows, qty_lots=qty_lots,
                 lot_size=E.NIFTY_LOT, reason=reason,
                 gross_rupees=sum(r["net_rupees"] + 0 for r in leg_rows),
                 net_rupees=total_net, charges_rupees=total_charges,
                 risk_rupees=(WING_OFFSET - credit) * E.NIFTY_LOT * qty_lots,
                 notes=f"expiry={expiry} credit={credit:.2f}")
    tr.gross_rupees = total_net + total_charges

    if reason != "time":
        eod_prices = {}
        ok = True
        for leg in legs:
            eod = E.day_end_price(con, leg["token"], exit_day)
            if eod is None:
                ok = False
                break
            eod_prices[leg["token"]] = eod[1]
        if ok:
            eod_net = 0.0
            for leg in legs:
                n, _ = E.leg_cost_rupees(leg["entry"], eod_prices[leg["token"]], E.NIFTY_LOT,
                                          qty_lots, slip, exit_day, leg["action"])
                eod_net += n
            tr.held_to_dayend_net_rupees = eod_net
            tr.held_to_dayend_note = f"structure marked at {exit_day} session close"
    return tr


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=0.02)
    ap.add_argument("--slippage", type=float, default=E.TICK)
    ap.add_argument("--wing-mode", choices=["fixed", "straddle-width"], default="fixed")
    ap.add_argument("--sw-multiplier", type=float, default=0.5,
                     help="straddle-width mode only: wing = short strike +/- "
                          "(ATM straddle premium x this)")
    ap.add_argument("--take-profit", type=float, default=TAKE_PROFIT)
    ap.add_argument("--label", default=None,
                     help="if set, save to results/sweeps/<NAME>_<label>.json "
                          "instead of results/<NAME>.json")
    args = ap.parse_args(argv)

    con = E.connect()
    expiries = E.list_expiries(con)
    trades, skips = [], {}
    n_floored = 0
    for expiry in expiries:
        entry_at, spot = monday_entry(con, expiry)
        if entry_at is None:
            skips["no_entry_bar"] = skips.get("no_entry_bar", 0) + 1
            continue
        chain = chain_at(con, expiry, entry_at)
        if chain is None:
            skips["chain_unpriced"] = skips.get("chain_unpriced", 0) + 1
            continue
        built = build_condor(con, expiry, entry_at, spot, *chain,
                              wing_mode=args.wing_mode, wing_offset=WING_OFFSET,
                              sw_multiplier=args.sw_multiplier)
        if built is None:
            skips["no_condor"] = skips.get("no_condor", 0) + 1
            continue
        legs, credit, max_loss = built
        # size_or_floor wants risk PER UNIT, not per lot -- see engine.py's
        # updated docstring. Fixed during the portfolio-sizing follow-up
        # pass; was passing an already-per-lot value, which double-divided
        # by lot_size and over-triggered the floor.
        qty_lots, floored, actual_risk_pct = E.size_or_floor(
            E.CAPITAL, args.risk, max_loss, E.NIFTY_LOT)
        risk_per_unit_rupees = max_loss * E.NIFTY_LOT  # per-LOT, for the note below
        if qty_lots <= 0:
            skips["qty_zero"] = skips.get("qty_zero", 0) + 1
            continue
        tr = run_condor(con, entry_at.date(), expiry, entry_at, legs, credit, qty_lots,
                         args.slippage, take_profit=args.take_profit)
        if tr is None:
            skips["no_walk_data"] = skips.get("no_walk_data", 0) + 1
            continue
        if floored:
            n_floored += 1
            tr.notes += (f" | SIZING_FLOOR: risk-based sizing gave 0 lots "
                         f"(1-lot max loss Rs{risk_per_unit_rupees:,.0f} > "
                         f"{args.risk*100:.0f}% risk budget); floored to 1 lot, "
                         f"actual risk {actual_risk_pct:.1f}% of capital")
        trades.append(tr)

    floored_pct = round(100 * n_floored / len(trades), 1) if trades else None
    save_name = f"sweeps/{NAME}_{args.label}" if args.label else NAME
    out, payload = E.save_results(save_name, trades, extra={
        "expiries_considered": len(expiries), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage,
        "params": {"target_delta": TARGET_DELTA, "wing_mode": args.wing_mode,
                    "wing_offset": WING_OFFSET, "sw_multiplier": args.sw_multiplier,
                    "take_profit_frac": args.take_profit, "stop_mult": STOP_MULT},
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
