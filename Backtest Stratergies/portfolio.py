"""Portfolio-level backtest: multiple strategies sharing ONE rolling capital
pool, up to N positions open at once, drawn from a diversified mix.

Why this exists (vs. the per-strategy results/*.json from the first pass):
those were each run as if the strategy had the FULL Rs 1,00,000 to itself,
one trade at a time, fixed (non-compounding) capital. That's fine for
judging a single strategy in isolation, but it isn't how the account would
actually be run -- one pool of capital, several strategies firing signals
on overlapping days, capital that compounds as trades close.

MECHANICS:
- Every trade a strategy WOULD have taken (per its original results/*.json)
  is a *candidate*. Whether it's actually taken here depends on: is a
  concurrency slot free (< max_concurrent open positions), and does taking
  it respect the diversification cap (<= max_per_strategy of the same
  strategy open at once)? If not, the candidate is SKIPPED -- recorded, not
  silently dropped, so the report can show what the concurrency cap cost.
- Every trade that IS taken is re-sized from scratch at CURRENT rolling
  equity (not the original fixed Rs 1,00,000), using each trade's own
  risk-per-lot (recoverable exactly from its original `risk_rupees /
  qty_lots`, since both scale linearly with lot count) and the SAME
  risk_pct and floor-or-skip policy the strategy originally used. Its net
  P&L at the new lot count is recomputed EXACTLY from the stored per-leg
  entry/exit premiums via `engine.leg_cost_rupees` -- not approximated by
  scaling the old net figure, which would mis-handle the flat per-order
  brokerage component.
- Equity updates at each trade's EXIT, in true chronological order relative
  to other trades' entries -- so a later signal can only use capital that
  has actually been freed up by then, exactly like a real account.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as E  # noqa: E402

HERE = Path(__file__).resolve().parent


@dataclass
class Candidate:
    strategy: str
    entry_at: datetime
    exit_at: datetime
    day: date
    legs: list
    lot_size: int
    orig_qty_lots: int
    orig_net_rupees: float
    risk_per_lot_rupees: float
    reason: str
    allow_floor: bool
    risk_pct: float
    slip: float


@dataclass
class Fill:
    strategy: str
    entry_at: datetime
    exit_at: datetime
    day: date
    qty_lots: int
    net_rupees: float
    equity_before: float
    equity_after: float


def load_candidates(manifest: list[dict]) -> list[Candidate]:
    """manifest: [{"strategy": display_name, "path": results-json path,
    "allow_floor": bool, "time_filter": optional (min_time, max_time) as
    datetime.time or None, "risk_pct_override": optional float}]"""
    out = []
    for entry in manifest:
        payload = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
        risk_pct = entry.get("risk_pct_override") or payload.get("extra", {}).get("risk_pct") or 0.02
        slip = payload.get("extra", {}).get("slippage") or E.TICK
        tf = entry.get("time_filter")
        for t in payload.get("trades", []):
            if not t.get("qty_lots") or not t.get("risk_rupees"):
                continue
            entry_at = datetime.fromisoformat(t["entry_at"])
            if tf is not None:
                lo, hi = tf
                if not (lo <= entry_at.time() <= hi):
                    continue
            exit_at = datetime.fromisoformat(t["exit_at"]) if t.get("exit_at") else entry_at
            risk_per_lot = t["risk_rupees"] / t["qty_lots"]
            if risk_per_lot <= 0:
                continue
            out.append(Candidate(
                strategy=entry["strategy"], entry_at=entry_at, exit_at=exit_at,
                day=date.fromisoformat(t["day"]), legs=t["legs"], lot_size=t["lot_size"],
                orig_qty_lots=t["qty_lots"], orig_net_rupees=t.get("net_rupees", 0.0),
                risk_per_lot_rupees=risk_per_lot,
                reason=t.get("reason", ""), allow_floor=entry.get("allow_floor", False),
                risk_pct=risk_pct, slip=slip,
            ))
    out.sort(key=lambda c: c.entry_at)
    return out


def reprice_legs(legs, lot_size, qty_lots, slip, day):
    total_net = 0.0
    for leg in legs:
        action = leg.get("action", "BUY")
        net, _ = E.leg_cost_rupees(leg["entry_premium"], leg["exit_premium"], lot_size,
                                    qty_lots, slip, day, action)
        total_net += net
    return total_net


def simulate(candidates: list[Candidate], capital: float, max_concurrent: int,
             max_per_strategy: int, max_qty_lots_per_trade: int | None = None,
             compounding: bool = True):
    """max_qty_lots_per_trade: a real-world liquidity/capacity cap. Full
    fixed-fractional compounding at 1-2% risk, across hundreds of positive-
    expectancy trades a year, mathematically compounds to an unrealistic
    equity size within a couple of years (verified: uncapped, this run
    reaches equity in the billions) -- a real account cannot actually put
    that much size into NIFTY weekly options without moving the market or
    running out of open interest to trade against. Capping lots per trade
    is a rough stand-in for that real constraint; it is NOT a precise
    liquidity model (this project has no order-book depth data to build
    one), just an honest acknowledgment that "the formula says N lots" and
    "N lots are actually fillable" are different claims once N gets large."""
    equity = capital
    open_positions: list[Fill] = []
    fills: list[Fill] = []
    skipped_no_slot = []
    skipped_zero_qty = []
    equity_curve = []  # (timestamp, equity) at every fill close

    def flush_closed(before_time):
        nonlocal equity
        still_open = []
        closing = [p for p in open_positions if p.exit_at <= before_time]
        closing.sort(key=lambda p: p.exit_at)
        for p in closing:
            equity += p.net_rupees
            equity_curve.append((p.exit_at, equity))
        for p in open_positions:
            if p.exit_at > before_time:
                still_open.append(p)
        open_positions[:] = still_open

    for c in candidates:
        flush_closed(c.entry_at)

        if len(open_positions) >= max_concurrent:
            skipped_no_slot.append(c)
            continue
        same_strategy_open = sum(1 for p in open_positions if p.strategy == c.strategy)
        if same_strategy_open >= max_per_strategy:
            skipped_no_slot.append(c)
            continue

        # size_or_floor wants risk PER UNIT, not per lot -- c.risk_per_lot_rupees
        # is (deliberately) per-lot, so divide back down before calling. Same
        # bug class documented at length in engine.py's size_or_floor docstring;
        # caught here by the fills-per-strategy sanity check during this run.
        risk_per_unit = c.risk_per_lot_rupees / c.lot_size
        sizing_base = equity if compounding else capital
        qty_lots, floored, _ = E.size_or_floor(sizing_base, c.risk_pct, risk_per_unit,
                                                c.lot_size, allow_floor=c.allow_floor)
        if qty_lots <= 0:
            skipped_zero_qty.append(c)
            continue
        if max_qty_lots_per_trade is not None:
            qty_lots = min(qty_lots, max_qty_lots_per_trade)

        net = reprice_legs(c.legs, c.lot_size, qty_lots, c.slip, c.day)
        fill = Fill(strategy=c.strategy, entry_at=c.entry_at, exit_at=c.exit_at, day=c.day,
                    qty_lots=qty_lots, net_rupees=net, equity_before=equity, equity_after=None)
        open_positions.append(fill)
        fills.append(fill)

    # drain remaining open positions
    if open_positions:
        last_time = max(p.exit_at for p in open_positions)
        flush_closed(last_time)

    for f in fills:
        pass  # equity_after not tracked per-fill individually; equity_curve has the series

    return {
        "fills": fills, "skipped_no_slot": skipped_no_slot,
        "skipped_zero_qty": skipped_zero_qty, "equity_curve": equity_curve,
        "final_equity": equity, "initial_capital": capital,
    }


def portfolio_metrics(result, capital):
    curve = result["equity_curve"]
    if not curve:
        return None
    peak = capital
    max_dd = 0.0
    max_dd_pct = 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd / peak if peak > 0 else 0)

    by_day: dict[date, float] = {}
    prev_eq = capital
    for ts, eq in curve:
        d = ts.date()
        by_day[d] = by_day.get(d, 0.0) + (eq - prev_eq)
        prev_eq = eq
    days = sorted(by_day)
    daily_returns = [by_day[d] / capital for d in days]
    import statistics, math
    sharpe = 0.0
    if len(daily_returns) > 1 and statistics.pstdev(daily_returns) > 0:
        sharpe = (statistics.mean(daily_returns) / statistics.pstdev(daily_returns)) * math.sqrt(252)

    span_days = (days[-1] - days[0]).days if len(days) > 1 else 1
    years = max(span_days / 365.0, 1 / 365.0)
    final_equity = result["final_equity"]
    cagr = (final_equity / capital) ** (1 / years) - 1 if final_equity > 0 else -1.0

    fills = result["fills"]
    wins = [f for f in fills if f.net_rupees > 0]
    losses = [f for f in fills if f.net_rupees <= 0]
    pf = (sum(f.net_rupees for f in wins) / abs(sum(f.net_rupees for f in losses))
          if losses and sum(f.net_rupees for f in losses) else float("inf"))

    by_strategy: dict[str, dict] = {}
    for f in fills:
        s = by_strategy.setdefault(f.strategy, {"n": 0, "net": 0.0, "wins": 0})
        s["n"] += 1
        s["net"] += f.net_rupees
        s["wins"] += 1 if f.net_rupees > 0 else 0

    return {
        "n_fills": len(fills), "n_skipped_no_slot": len(result["skipped_no_slot"]),
        "n_skipped_zero_qty": len(result["skipped_zero_qty"]),
        "win_rate_pct": round(100 * len(wins) / len(fills), 2) if fills else None,
        "net_total_rupees": round(final_equity - capital, 2),
        "final_equity": round(final_equity, 2),
        "profit_factor": round(pf, 3) if pf != float("inf") else None,
        "max_drawdown_rupees": round(max_dd, 2),
        "max_drawdown_pct_of_peak": round(100 * max_dd_pct, 2),
        "cagr_pct": round(100 * cagr, 2),
        "sharpe": round(sharpe, 3),
        "date_span": f"{days[0]}..{days[-1]}" if days else "",
        "by_strategy": {k: {"n": v["n"], "net_rupees": round(v["net"], 2),
                             "win_rate_pct": round(100 * v["wins"] / v["n"], 1)}
                         for k, v in by_strategy.items()},
        "missed_due_to_no_slot_original_sizing_net_rupees": round(
            sum(c.orig_net_rupees for c in result["skipped_no_slot"]), 2),
        "missed_due_to_no_slot_count": len(result["skipped_no_slot"]),
    }
