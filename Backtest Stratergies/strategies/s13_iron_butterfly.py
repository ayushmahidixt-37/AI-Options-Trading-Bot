"""#13 Iron Butterfly -- spec item #13, "reference-only variants" section.

Spec text is minimal ("short ATM straddle + OTM wings 100-300 pts out",
pointing at a `strategy-reference-catalog.md` that does not exist in this
repo) -- there is no fuller spec to find; this is genuinely under-specified
and the choices below are documented judgment calls, not discovered facts.

Reasonable literal build implemented:
  Entry     Monday of expiry week (or next available day), 09:30-09:45 --
            no other timing is given anywhere in the spec for this
            strategy, so this borrows s07 Iron Condor's entry convention
            as the closest sibling strategy in this batch.
  Structure short ATM call + short ATM put, BOTH at `E.atm_strike(spot,
            50)` -- unlike s07's delta-selected short strikes, an Iron
            BUTTERFLY by definition sells straight at the money; that IS
            the distinguishing feature from a Condor, so this is the one
            deliberate structural difference from s07's build.
  Wings     long call/put hedges 200 points further OTM (the middle of the
            spec's stated 100-300 point range, and equal to s07's own
            WING_OFFSET so the two strategies are directly comparable on
            wing width).
  Stop/TP   combined structure stop at 1.5x net credit lost, target at 70%
            of net credit captured -- carried straight over from s07 in
            the total absence of butterfly-specific numbers in the spec;
            flagged here rather than presented as a discovered parameter.
  Time      force close by 15:00 on expiry day.

This shares essentially all of its walk/cost/day-end-counterfactual logic
with s07_iron_condor.py (duplicated here rather than imported, per this
project's convention that every strategy script is self-contained -- see
e.g. how clean_room/*.py each duplicate their own copy of shared logic).
The only real difference from s07's `build_condor` is that both short legs
are pinned to the ATM strike instead of walking the chain for a target
delta.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s13_iron_butterfly"
WING_OFFSET = 200.0
TAKE_PROFIT = 0.70
STOP_MULT = 1.5
STRIKE_STEP = 50.0


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
    return meta, {r[0]: float(r[1]) for r in priced}


def build_butterfly(expiry, spot, meta, priced):
    atm = E.atm_strike(spot, STRIKE_STEP)
    by_key = {}
    for token, ltp in priced.items():
        strike, otype = meta[token]
        by_key[(otype, strike)] = {"token": token, "ltp": ltp}
    sc = by_key.get(("CE", atm))
    sp = by_key.get(("PE", atm))
    lc = by_key.get(("CE", atm + WING_OFFSET))
    lp = by_key.get(("PE", atm - WING_OFFSET))
    if sc is None or sp is None or lc is None or lp is None:
        return None
    credit = (sc["ltp"] + sp["ltp"]) - (lc["ltp"] + lp["ltp"])
    if credit <= 0:
        return None
    legs = [
        {"type": "CE", "strike": atm, "action": "SELL", "token": sc["token"], "entry": sc["ltp"]},
        {"type": "PE", "strike": atm, "action": "SELL", "token": sp["token"], "entry": sp["ltp"]},
        {"type": "CE", "strike": atm + WING_OFFSET, "action": "BUY", "token": lc["token"], "entry": lc["ltp"]},
        {"type": "PE", "strike": atm - WING_OFFSET, "action": "BUY", "token": lp["token"], "entry": lp["ltp"]},
    ]
    max_loss_per_unit = WING_OFFSET - credit
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


def run_butterfly(con, day, expiry, entry_at, legs, credit, qty_lots, slip):
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
        if profit >= TAKE_PROFIT * credit:
            reason = "target"
            break
        if profit <= -STOP_MULT * credit:
            reason = "stop"
            break
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
                 direction="iron_butterfly", legs=leg_rows, qty_lots=qty_lots,
                 lot_size=E.NIFTY_LOT, reason=reason,
                 gross_rupees=total_net + total_charges, net_rupees=total_net,
                 charges_rupees=total_charges,
                 risk_rupees=(WING_OFFSET - credit) * E.NIFTY_LOT * qty_lots,
                 notes=f"expiry={expiry} credit={credit:.2f}")

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
    args = ap.parse_args(argv)

    con = E.connect()
    expiries = E.list_expiries(con)
    trades, skips = [], {}
    for expiry in expiries:
        entry_at, spot = monday_entry(con, expiry)
        if entry_at is None:
            skips["no_entry_bar"] = skips.get("no_entry_bar", 0) + 1
            continue
        chain = chain_at(con, expiry, entry_at)
        if chain is None:
            skips["chain_unpriced"] = skips.get("chain_unpriced", 0) + 1
            continue
        built = build_butterfly(expiry, spot, *chain)
        if built is None:
            skips["no_butterfly"] = skips.get("no_butterfly", 0) + 1
            continue
        legs, credit, max_loss = built
        # size_or_floor wants risk PER UNIT, not per lot -- fixed during the
        # portfolio-sizing follow-up pass (see s09/s11/s07 for the same fix
        # and full explanation; passing a per-lot value here double-divided
        # by lot_size internally and over-triggered the floor).
        qty_lots, floored, actual_risk_pct = E.size_or_floor(
            E.CAPITAL, args.risk, max_loss, E.NIFTY_LOT)
        risk_per_unit_rupees = max_loss * E.NIFTY_LOT  # per-LOT, for the note below
        if qty_lots <= 0:
            skips["qty_zero"] = skips.get("qty_zero", 0) + 1
            continue
        tr = run_butterfly(con, entry_at.date(), expiry, entry_at, legs, credit, qty_lots, args.slippage)
        if tr is None:
            skips["no_walk_data"] = skips.get("no_walk_data", 0) + 1
            continue
        if floored:
            tr.notes += (f" | SIZING_FLOOR: risk-based sizing gave 0 lots "
                         f"(1-lot max loss Rs{risk_per_unit_rupees:,.0f} > "
                         f"{args.risk*100:.0f}% risk budget); floored to 1 lot, "
                         f"actual risk {actual_risk_pct:.1f}% of capital")
        trades.append(tr)

    out, payload = E.save_results(NAME, trades, extra={
        "spec_note": (
            "Spec item #13 gives only 'short ATM straddle + OTM wings "
            "100-300 pts out' and points at a strategy-reference-catalog.md "
            "that does not exist in this repo. Entry timing, wing width "
            "(200, midpoint of the stated range), and the 70%/1.5x TP/SL "
            "are all judgment calls carried over from s07 Iron Condor, not "
            "discovered spec values -- see this file's module docstring."),
        "expiries_considered": len(expiries), "skips": skips, "risk_pct": args.risk,
        "slippage": args.slippage,
        "params": {"wing_offset": WING_OFFSET, "take_profit_frac": TAKE_PROFIT,
                    "stop_mult": STOP_MULT, "strike_step": STRIKE_STEP},
    })
    m = payload["metrics"]
    print(f"{NAME}: {len(expiries)} expiries -> {len(trades)} cycles (skips: {skips})")
    if m:
        print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
              f"PF {m['profit_factor']} | maxDD {m['max_drawdown_pct_of_peak']}% | "
              f"CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
