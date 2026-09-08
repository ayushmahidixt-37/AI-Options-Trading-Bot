"""Live paper-trading runner for the 5 strategies validated in
`Backtest Stratergies/` (parameters: CONFIG.md; results: PHASE3_REPORT.md).

WHY THIS FILE EXISTS RATHER THAN A REWRITE INSIDE strategy_experimental.py:
this project already lost a strategy to exactly that pattern once --
"Candidate B" was independently re-implemented for the live path, and its
live-wired parameters silently drifted from what was actually backtested;
the confirmation was retracted when it couldn't reproduce (see
PROJECT_STATUS.md's 2026-08-26 entries). This module imports the SAME
strategy files the backtest used (`Backtest Stratergies/strategies/*.py`,
`Backtest Stratergies/engine.py`) rather than re-deriving their rules --
there is exactly one copy of each strategy's logic, used for both jobs.

WHAT IS REUSED FROM THE EXISTING LIVE SYSTEM, AND WHY: ConnectionManager
(this module) already does live NIFTY price polling, live option-instrument
resolution (`archive.select_near_atm_options`), and live option quoting
(`quote_instrument`) -- all already exercised by Candidate B's live path.
Re-implementing broker connectivity a second time would be strictly riskier
than reusing code that already works, so only the DECISION logic is new
here, not the market-data/broker plumbing. Settings.validate() hard-enforces
trading_mode=="paper" and live_trading_enabled==False before any of this can
run at all -- this file adds no order-placement capability beyond what
PaperBroker already offers (see paper_broker.py: "contains no broker order
API").

ACTIVE_STRATEGIES below is the one thing to edit to run 1 strategy or all 5
-- nothing else in this file, or in CONFIG.md, needs to change either way.

HOW LIVE SIGNAL DETECTION WORKS WITHOUT DUPLICATING THE BACKTEST'S EXIT
LOGIC: for #3 and #4 (both built on engine.walk_signal), this module calls
the exact same `signals(bars_so_far, days_so_far)` function fresh every
cycle, against however much of today's session has actually happened so
far. Because there's no data past "now", any position genuinely still open
comes back with reason="session_end" and an exit_at equal to the LATEST bar
loaded -- indistinguishable, by construction, from a true end-of-day close
UNLESS that latest bar's own clock time is checked against the real
session close (15:29). `_is_real_exit` below is that check: a "stop" or
"target"/"signal" reason is always final; a "session_end" reason is only
final once the bar time itself reaches the real close. This lets the
exact backtest function double as the live incremental evaluator with zero
duplicated exit logic -- re-run each cycle, not re-written.

#7/#10/#12 don't use walk_signal (their entries are clock-time-triggered,
not continuously scanned), so their live adapters instead port each
strategy's own threshold CONSTANTS and comparison logic (imported from the
strategy module, not retyped) against live quotes each cycle -- see each
adapter's docstring for exactly what's reused vs. re-expressed.

CRITICAL: ALL option instrument resolution and ALL option/index PRICING in
this file goes through `connections` (live), never through `E.connect()`'s
static backtest archive. Checked directly before relying on this: that
archive's `instruments` table has no expiry past 2026-09-03 and no priced
`market_candles` row past 2026-08-28 -- it is a fixed historical snapshot,
not a live-updating source, and using it for "current" price/instrument
lookups would silently return stale nonsense once today's date is past
that point (which it already is). `E.connect()` here is used ONLY for
pure-reference lookups that don't change day to day if at all necessary,
and pure-math helpers (`E.leg_cost_rupees`, `E.bs_delta`, `E.size_or_floor`,
`E.implied_vol`) that take their inputs as arguments rather than querying
the DB themselves. Live prices come from `connections.quote_instrument`;
live instrument/token resolution comes from
`connections.archive.select_near_atm_options` (the archive ConnectionManager
itself keeps fresh via its own background refresh, independent of and much
newer than the static backtest snapshot).

CAPITAL MODEL: matches PHASE3_REPORT.md exactly -- one shared, NEVER
REINVESTED Rs 1,00,000, sized fresh each trade off that fixed figure (not
off running paper P&L), up to 5 concurrent positions, up to 2 concurrent
per single strategy (both caps confirmed in backtest to never actually
bind with this roster, but enforced here regardless).
"""

from __future__ import annotations

import json
import sys
import time as time_module
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_DIR = REPO_ROOT / "Backtest Stratergies"
sys.path.insert(0, str(BACKTEST_DIR))
sys.path.insert(0, str(BACKTEST_DIR / "strategies"))

import engine as E  # noqa: E402 -- the SAME engine.py the backtest used
import s03_orb  # noqa: E402
import s04_supertrend_vwap  # noqa: E402
import s07_iron_condor  # noqa: E402
import s10_short_straddle_920  # noqa: E402
import s12_straddle_breakeven_banknifty  # noqa: E402

from .config import Settings, load_env_file  # noqa: E402
from .connections import ConnectionManager, ConnectionActionError  # noqa: E402
from .domain import Instrument  # noqa: E402
from .clock import MarketClock  # noqa: E402

DEFAULT_CONFIG_PATH = REPO_ROOT / "local-bot.env"

# ----------------------------------------------------------------- config
ACTIVE_STRATEGIES = [
    "s03_orb",
    "s04_supertrend_vwap",
    "s07_iron_condor",
    "s10_short_straddle_920",
    "s12_straddle_breakeven_banknifty",
]  # edit this list only -- 1 strategy, all 5, or anything in between

CAPITAL = E.CAPITAL          # Rs 1,00,000, never reinvented (see module docstring)
RISK_PCT = 0.02
MAX_CONCURRENT = 5
MAX_PER_STRATEGY = 2
CYCLE_SECONDS = 60            # how often run_cycle() re-evaluates strategies
POLL_SECONDS = 15             # how often the spot price is sampled into bars --
                               # must be well under 60s or every 1-min bar
                               # degenerates to a single point (O=H=L=C),
                               # starving Supertrend's ATR of real range.
                               # Found by direct replay-testing against a real
                               # historical day before trusting this at all.
LEDGER_PATH = REPO_ROOT / "live_paper_trading" / "ledger.jsonl"
STATE_PATH = REPO_ROOT / "live_paper_trading" / "state.json"


# ------------------------------------------------------------------ ledger
@dataclass
class LiveTrade:
    strategy: str
    day: str
    entry_at: str
    exit_at: str | None
    direction: str
    legs: list
    qty_lots: int
    lot_size: int
    reason: str
    net_rupees: float
    status: str  # "open" or "closed"
    key: str     # unique id for de-duplicating across cycles


class LiveLedger:
    """Append-only JSON-lines file -- same Trade.as_row() shape engine.py's
    backtest already writes, so PHASE-style analysis scripts work unchanged
    on live results too."""

    def __init__(self, path: Path = LEDGER_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.trades: dict[str, LiveTrade] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                self.trades[row["key"]] = LiveTrade(**row)

    def open_positions(self, strategy: str | None = None) -> list[LiveTrade]:
        out = [t for t in self.trades.values() if t.status == "open"]
        return [t for t in out if t.strategy == strategy] if strategy else out

    def all_open(self) -> list[LiveTrade]:
        return self.open_positions()

    def record(self, trade: LiveTrade) -> None:
        self.trades[trade.key] = trade
        self._rewrite()

    def _rewrite(self) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            for t in self.trades.values():
                fh.write(json.dumps(asdict(t)) + "\n")

    def realized_pnl(self) -> float:
        return sum(t.net_rupees for t in self.trades.values() if t.status == "closed")


class RunState:
    """Small persisted cursor state -- which signals/entries this process
    has already acted on, so a restart mid-day doesn't double-enter."""

    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, default=str), encoding="utf-8")


# ------------------------------------------------------------- bar buffer
SEED_TRADING_DAYS = 10  # calendar days of lookback used to source the seed


class LiveBarBuffer:
    """Builds real 1-minute NIFTY OHLC bars from polled spot prices --
    engine.py's exact (datetime, o, h, l, c) tuple shape, so every strategy
    function that expects backtest bars works unmodified on these.

    SEEDING MATTERS: Supertrend's ATR is Wilder-smoothed continuously
    across the backtest's entire multi-year bar series -- it never resets
    between days. A live buffer that only held TODAY's bars would cold-
    start that smoothing every single morning, producing materially wrong
    Supertrend values (and, found by direct replay-testing against a real
    historical day before trusting this: spurious back-to-back entries a
    few minutes apart) until enough of the day had passed to converge.
    `seed()` pre-loads recent real history from the backtest archive at
    startup specifically to avoid that -- using slightly-stale historical
    bars purely to warm up an indicator's internal state is standard
    practice and not the same class of problem as pricing off stale data
    (see the module docstring's warning about that, which this is not)."""

    def __init__(self):
        self.bars: list[tuple] = []
        self._cur_minute: datetime | None = None
        self._o = self._h = self._l = self._c = None

    def seed(self, con, before: date, calendar_days: int = SEED_TRADING_DAYS) -> None:
        start = before - timedelta(days=calendar_days)
        seed_bars = E.filter_session(E.load_index_bars(
            con, start=f"{start.isoformat()}T00:00:00+05:30",
            end=f"{before.isoformat()}T00:00:00+05:30"))
        self.bars = seed_bars

    def add_tick(self, ts: datetime, price: float) -> None:
        minute = ts.replace(second=0, microsecond=0)
        if self._cur_minute is None:
            self._cur_minute, self._o, self._h, self._l, self._c = minute, price, price, price, price
            return
        if minute == self._cur_minute:
            self._h = max(self._h, price)
            self._l = min(self._l, price)
            self._c = price
            return
        # minute rolled over -- finalize the previous one
        self.bars.append((self._cur_minute, self._o, self._h, self._l, self._c))
        self._cur_minute, self._o, self._h, self._l, self._c = minute, price, price, price, price

    def bars_for_signals(self) -> list[tuple]:
        """Seed history + everything finalized so far + the still-forming
        current minute as a provisional last bar (so signals can react
        within the same minute rather than one full minute late). Callers
        must filter the resulting Signal/trade list to today's date --
        this deliberately returns multi-day history so indicators stay
        warmed up (see class docstring); it is not itself "today's bars"."""
        out = list(self.bars)
        if self._cur_minute is not None:
            out.append((self._cur_minute, self._o, self._h, self._l, self._c))
        return out


# ------------------------------------------------------------ exit finality
_REAL_SESSION_CLOSE = dtime(15, 29)


def _is_real_exit(reason: str, exit_at: datetime) -> bool:
    """True if this exit is genuinely final, not an artifact of the replay
    simply not having any later bars loaded yet (see module docstring)."""
    if reason not in ("session_end", "time", "time_exit", ""):
        return True
    return exit_at.time() >= _REAL_SESSION_CLOSE


def _key(strategy: str, day: date, entry_at: datetime, tag: str = "") -> str:
    return f"{strategy}|{day.isoformat()}|{entry_at.isoformat()}|{tag}"


# ----------------------------------------------------------- sizing helper
def _size(ledger: LiveLedger, risk_per_lot_rupees: float, allow_floor: bool) -> tuple[int, bool]:
    open_now = ledger.all_open()
    if len(open_now) >= MAX_CONCURRENT:
        return 0, False
    qty, floored, _ = E.size_or_floor(CAPITAL, RISK_PCT, risk_per_lot_rupees / E.NIFTY_LOT,
                                       E.NIFTY_LOT, allow_floor=allow_floor)
    return qty, floored


def _slot_available(ledger: LiveLedger, strategy: str) -> bool:
    if len(ledger.all_open()) >= MAX_CONCURRENT:
        return False
    if len(ledger.open_positions(strategy)) >= MAX_PER_STRATEGY:
        return False
    return True


# --------------------------------------------------------- live instruments
def live_chain(connections, today: date, spot: float, band: int = 15):
    """A live-priced instrument band around ATM, nearest expiry >= today --
    via the already-tested live archive, NOT the static backtest DB (see
    module docstring for why that distinction is load-bearing here)."""
    return connections.archive.select_near_atm_options(today, spot, band)


def find_instrument(chain, strike: float, option_type: str):
    for inst in chain:
        if inst.strike == strike and inst.option_type == option_type:
            return inst
    return None


def live_price(connections, instrument, now: datetime) -> float | None:
    try:
        return connections.quote_instrument(instrument, now).price
    except ConnectionActionError:
        return None


def live_quote_full(connections, instrument, now: datetime):
    """Like live_price but keeps the quote's own observed_at -- the real
    fill moment, which can trail the signal timestamp (e.g. a stale/gapped
    quote) and should be what gets recorded as entry_at, not the intended
    signal time. Found to matter via direct replay-testing: the archive
    used for that test has real timestamp gaps in its 1-min option data,
    which is exactly the scenario this distinction is for."""
    try:
        q = connections.quote_instrument(instrument, now)
        return q.price, q.observed_at
    except ConnectionActionError:
        return None, None


def _inst_row(inst) -> dict:
    """Plain-dict, JSON-serializable snapshot of the bits of an Instrument
    needed to re-quote it later -- Instrument itself is a frozen dataclass,
    fine in memory, but the ledger is JSON-lines so only plain fields go in."""
    return {"token": inst.token, "exchange": inst.exchange, "symbol": inst.symbol,
            "lot_size": inst.lot_size, "option_type": inst.option_type}


def _inst_from_row(row: dict):
    return Instrument(symbol=row["symbol"], token=row["token"], exchange=row["exchange"],
                       underlying="NIFTY", option_type=row["option_type"], lot_size=row["lot_size"])


# --------------------------------------------------- #3 / #4 (walk_signal)
def run_walk_signal_strategy(name: str, module, resample_fn, ledger: LiveLedger,
                              buffer: LiveBarBuffer, connections,
                              today: date, now: datetime):
    """`resample_fn` takes bars only (e.g. module.resample_5min) -- both
    adopted configs (#3, #4) use a fixed 5-min timeframe, so no separate
    minutes argument is needed here. Runs the signal function over the
    FULL seeded multi-day buffer (see LiveBarBuffer's docstring for why
    that matters for #4's Supertrend specifically) and then only acts on
    signals dated today -- everything from the seed history is either
    already-known past trades or pure indicator warm-up, never something
    to act on live."""
    bars_1m = buffer.bars_for_signals()
    if len(bars_1m) < 2:
        return
    bars_tf = resample_fn(bars_1m) if resample_fn is not None else bars_1m
    days_tf = E.day_index(bars_tf)
    if not days_tf:
        return
    sigs = [s for s in module.signals(bars_tf, days_tf) if s.day == today]
    spot = connections.snapshot().nifty_price
    if not spot:
        return
    for s in sigs:
        k = _key(name, s.day, s.entry_at)
        existing = ledger.trades.get(k)
        final = _is_real_exit(s.reason, s.exit_at)
        if existing is None:
            if not _slot_available(ledger, name):
                continue
            option_type = "CE" if s.side == "long" else "PE"
            strike = E.atm_strike(s.entry_index)
            chain = live_chain(connections, today, spot)
            inst = find_instrument(chain, strike, option_type)
            if inst is None:
                continue
            entry_prem, fill_at = live_quote_full(connections, inst, s.entry_at)
            if not entry_prem or entry_prem <= 0:
                continue
            fill_at = fill_at or s.entry_at  # the dedup key `k` still uses s.entry_at
            index_risk = abs(s.entry_index - s.stop_index)
            # No live IV feed -> no live delta; size_by_index_risk already
            # falls back to delta=0.5 when delta_abs is None (see
            # engine.py), the same approximation the backtest documents
            # for missing-IV cases -- not a new gap introduced here.
            qty_lots = E.size_by_index_risk(CAPITAL, RISK_PCT, index_risk, None, E.NIFTY_LOT)
            if qty_lots <= 0:
                continue
            leg = {"type": option_type, "strike": strike, "action": "BUY",
                   "token": inst.token, "entry_premium": entry_prem}
            if final:
                exit_prem = live_price(connections, inst, now) or entry_prem
                net, _ = E.leg_cost_rupees(entry_prem, exit_prem, E.NIFTY_LOT, qty_lots,
                                            E.TICK, s.day, "BUY")
                leg = {**leg, "exit_premium": exit_prem, "net_rupees": round(net, 2)}
            ledger.record(LiveTrade(
                strategy=name, day=s.day.isoformat(), entry_at=fill_at.isoformat(),
                exit_at=s.exit_at.isoformat() if final else None,
                direction=f"long_{option_type.lower()}", legs=[leg], qty_lots=qty_lots,
                lot_size=E.NIFTY_LOT, reason=s.reason if final else "still_open",
                net_rupees=leg.get("net_rupees", 0.0), status="closed" if final else "open", key=k))
        elif existing.status == "open" and final:
            close_open_option_position(connections, ledger, existing, s.exit_at, s.reason, now)


def close_open_option_position(connections, ledger: LiveLedger, existing: LiveTrade,
                                exit_at: datetime, reason: str, now: datetime) -> None:
    leg = existing.legs[0]
    spot = connections.snapshot().nifty_price
    if not spot:
        return
    chain = live_chain(connections, date.fromisoformat(existing.day), spot)
    inst = find_instrument(chain, leg["strike"], leg["type"])
    if inst is None:
        return
    exit_prem = live_price(connections, inst, now)
    if exit_prem is None:
        exit_prem = leg["entry_premium"]
    net, _ = E.leg_cost_rupees(leg["entry_premium"], exit_prem, existing.lot_size,
                                existing.qty_lots, E.TICK, date.fromisoformat(existing.day), "BUY")
    existing.exit_at = exit_at.isoformat()
    existing.reason = reason
    existing.net_rupees = net
    existing.status = "closed"
    existing.legs = [{**leg, "exit_premium": exit_prem, "net_rupees": round(net, 2)}]
    ledger.record(existing)


# ---------------------------------------------------------------- #7 condor
def run_iron_condor(ledger: LiveLedger, connections, today: date, now: datetime):
    """Entry: same delta-target strike selection as s07_iron_condor.py
    (TARGET_DELTA, WING_OFFSET imported, not retyped), but delta comes from
    `E.implied_vol` inverted from the live quote rather than a stored IV
    field -- live quotes carry no IV, see engine.py's implied_vol docstring
    for why inversion is a faithful equivalent, not an approximation of a
    different kind. Exit: ports TAKE_PROFIT/STOP_MULT (imported, not
    retyped) and the same `profit = credit - value` comparison s07 uses,
    against live quotes each cycle instead of a historical leg_series query."""
    mod = s07_iron_condor
    spot = connections.snapshot().nifty_price
    if not spot:
        return
    chain = live_chain(connections, today, spot, band=15)
    if not chain:
        return
    expiry = chain[0].expiry
    k = _key("s07_iron_condor", expiry, datetime.combine(expiry, dtime(9, 30), tzinfo=E.IST))
    existing = ledger.trades.get(k)
    if existing is None:
        if now.weekday() != 0 or not (dtime(9, 30) <= now.time() <= dtime(9, 45)):
            return
        if not _slot_available(ledger, "s07_iron_condor"):
            return
        yrs = E.years_to_expiry(expiry, now)
        priced = {}
        for inst in chain:
            price = live_price(connections, inst, now)
            if price is None:
                continue
            iv = E.implied_vol(price, spot, inst.strike, yrs, inst.option_type)
            delta = E.bs_delta(spot, inst.strike, yrs, iv, inst.option_type) if iv else None
            priced[(inst.option_type, inst.strike)] = {"inst": inst, "price": price, "delta": delta}
        calls = [(k2, v) for k2, v in priced.items() if k2[0] == "CE" and v["delta"] is not None]
        puts = [(k2, v) for k2, v in priced.items() if k2[0] == "PE" and v["delta"] is not None]
        if not calls or not puts:
            return
        (_, sc_strike), sc = min(calls, key=lambda kv: abs(kv[1]["delta"] - mod.TARGET_DELTA))
        (_, sp_strike), sp = min(puts, key=lambda kv: abs(kv[1]["delta"] + mod.TARGET_DELTA))
        lc = priced.get(("CE", sc_strike + mod.WING_OFFSET))
        lp = priced.get(("PE", sp_strike - mod.WING_OFFSET))
        if lc is None or lp is None:
            return
        credit = (sc["price"] + sp["price"]) - (lc["price"] + lp["price"])
        if credit <= 0:
            return
        max_loss = mod.WING_OFFSET - credit
        qty, _ = _size(ledger, max_loss * E.NIFTY_LOT, allow_floor=True)
        if qty <= 0:
            return
        leg_rows = [
            {"type": "CE", "strike": sc_strike, "action": "SELL", "token": sc["inst"].token, "entry_premium": sc["price"]},
            {"type": "PE", "strike": sp_strike, "action": "SELL", "token": sp["inst"].token, "entry_premium": sp["price"]},
            {"type": "CE", "strike": sc_strike + mod.WING_OFFSET, "action": "BUY", "token": lc["inst"].token, "entry_premium": lc["price"]},
            {"type": "PE", "strike": sp_strike - mod.WING_OFFSET, "action": "BUY", "token": lp["inst"].token, "entry_premium": lp["price"]},
        ]
        ledger.record(LiveTrade(
            strategy="s07_iron_condor", day=today.isoformat(),
            entry_at=now.isoformat(), exit_at=None, direction="iron_condor",
            legs=leg_rows, qty_lots=qty, lot_size=E.NIFTY_LOT, reason="", net_rupees=0.0,
            status="open", key=k))
        return

    if existing.status != "open":
        return
    time_exit = datetime(expiry.year, expiry.month, expiry.day, 15, 0, tzinfo=E.IST)
    prices = {}
    for leg in existing.legs:
        inst = find_instrument(chain, leg["strike"], leg["type"])
        if inst is None:
            return
        p = live_price(connections, inst, now)
        if p is None:
            return
        prices[leg["strike"], leg["type"]] = p
    value = sum(prices[l["strike"], l["type"]] if l["action"] == "SELL" else -prices[l["strike"], l["type"]]
                for l in existing.legs)
    credit = sum(l["entry_premium"] if l["action"] == "SELL" else -l["entry_premium"]
                 for l in existing.legs)
    profit = credit - value
    reason = None
    if profit >= mod.TAKE_PROFIT * credit:
        reason = "target"
    elif profit <= -mod.STOP_MULT * credit:
        reason = "stop"
    elif now >= time_exit:
        reason = "time"
    if reason is None:
        return
    total_net = 0.0
    leg_rows = []
    for leg in existing.legs:
        p = prices[leg["strike"], leg["type"]]
        net, _ = E.leg_cost_rupees(leg["entry_premium"], p, existing.lot_size,
                                    existing.qty_lots, E.TICK, now.date(), leg["action"])
        total_net += net
        leg_rows.append({**leg, "exit_premium": p, "net_rupees": round(net, 2)})
    existing.exit_at = now.isoformat()
    existing.reason = reason
    existing.net_rupees = total_net
    existing.status = "closed"
    existing.legs = leg_rows
    ledger.record(existing)


# ------------------------------------------------------- #10 / #12 straddles
def run_two_leg_stop_strategy(strategy_name: str, ledger: LiveLedger, connections, today: date,
                               now: datetime, entry_time: dtime, strike_step: float,
                               sl_mult: dict, sl_buffer: float, square_off: dtime,
                               reentry_time: dtime, breakeven: bool):
    """Shared live adapter for #10 (Short Straddle 9:20) and #12 (Short
    Straddle Breakeven) -- same shape (ATM straddle, independent per-leg
    stop, one 12:30 re-entry if both legs already stopped), differing only
    in entry time, strike-rounding step, per-leg stop %, buffer, and
    whether a stopped leg's survivor gets breakeven-adjusted. Reuses each
    strategy's own SL_MULT/SL_BUFFER values (passed in by the caller from
    the imported module, not retyped) for the trigger formula, which is
    the one piece of comparison logic ported rather than called directly
    (s10/s12's own walk_legs* functions replay a historical DB query this
    live loop doesn't have -- see module docstring)."""
    day_key = f"{strategy_name}|{today.isoformat()}"
    state = getattr(run_two_leg_stop_strategy, "_state", {})
    run_two_leg_stop_strategy._state = state
    day_state = state.setdefault(day_key, {"phase": "pending", "legs": None, "reentry_done": False})

    def open_new(at_time_ok: bool, is_reentry: bool):
        if day_state["phase"] not in ("pending",):
            return
        if not at_time_ok:
            return
        if not _slot_available(ledger, strategy_name):
            return
        spot = connections.snapshot().nifty_price
        if not spot:
            return
        atm = E.atm_strike(spot, strike_step)
        chain = live_chain(connections, today, spot)
        ce_inst = find_instrument(chain, atm, "CE")
        pe_inst = find_instrument(chain, atm, "PE")
        if ce_inst is None or pe_inst is None:
            return
        ce_p, pe_p = live_price(connections, ce_inst, now), live_price(connections, pe_inst, now)
        if ce_p is None or pe_p is None:
            return
        risk_per_lot = (ce_p * sl_mult["CE"] + (0 if breakeven else sl_buffer)
                         + pe_p * sl_mult["PE"] + (0 if breakeven else sl_buffer)) * E.NIFTY_LOT
        qty, _ = _size(ledger, risk_per_lot, allow_floor=True)
        if qty <= 0:
            return
        legs = {
            "CE": {"inst": ce_inst, "strike": atm, "entry": ce_p, "open": True,
                   "stop": ce_p * (1 + sl_mult["CE"]) + (0 if breakeven else sl_buffer)},
            "PE": {"inst": pe_inst, "strike": atm, "entry": pe_p, "open": True,
                   "stop": pe_p * (1 + sl_mult["PE"]) + (0 if breakeven else sl_buffer)},
        }
        day_state.update(phase="open", legs=legs, qty=qty, reentry_done=is_reentry or day_state["reentry_done"])
        k = _key(strategy_name, today, now, "reentry" if is_reentry else "main")
        ledger.record(LiveTrade(
            strategy=strategy_name, day=today.isoformat(), entry_at=now.isoformat(), exit_at=None,
            direction="short_straddle",
            legs=[{"type": t, "strike": v["strike"], "entry_premium": v["entry"], **_inst_row(v["inst"])}
                  for t, v in legs.items()],
            qty_lots=qty, lot_size=E.NIFTY_LOT, reason="", net_rupees=0.0, status="open", key=k))
        day_state["ledger_key"] = k

    if day_state["phase"] == "pending":
        open_new(now.time() >= entry_time, is_reentry=False)
        return
    if day_state["phase"] != "open":
        return

    legs = day_state["legs"]
    for otype, leg in legs.items():
        if not leg["open"]:
            continue
        p = live_price(connections, leg["inst"], now)
        if p is None:
            continue
        if p >= leg["stop"]:
            leg["open"] = False
            leg["exit"] = p
            leg["reason"] = "breakeven_stop" if leg.get("is_breakeven") else "leg_sl"
            if breakeven:
                other = "PE" if otype == "CE" else "CE"
                if legs[other]["open"] and not legs[other].get("is_breakeven"):
                    legs[other]["stop"] = legs[other]["entry"]
                    legs[other]["is_breakeven"] = True

    both_stopped = not legs["CE"]["open"] and not legs["PE"]["open"]
    time_exit_due = now.time() >= square_off
    if both_stopped or time_exit_due:
        total_net, leg_rows = 0.0, []
        for otype, leg in legs.items():
            if leg["open"]:
                p = live_price(connections, leg["inst"], now) or leg["entry"]
                leg["exit"], leg["reason"] = p, "time_exit"
            net, _ = E.leg_cost_rupees(leg["entry"], leg["exit"], E.NIFTY_LOT, day_state["qty"],
                                        E.TICK, today, "SELL")
            total_net += net
            leg_rows.append({"type": otype, "strike": leg["strike"], "entry_premium": leg["entry"],
                              "exit_premium": leg["exit"], "reason": leg["reason"], "net_rupees": round(net, 2)})
        existing = ledger.trades.get(day_state["ledger_key"])
        if existing is not None:
            existing.exit_at = now.isoformat()
            existing.reason = "+".join(sorted({r["reason"] for r in leg_rows}))
            existing.net_rupees = total_net
            existing.status = "closed"
            existing.legs = leg_rows
            ledger.record(existing)
        if both_stopped and not time_exit_due and now.time() <= reentry_time and not day_state["reentry_done"]:
            day_state["phase"] = "pending"
            if now.time() >= reentry_time:
                open_new(True, is_reentry=True)
        else:
            day_state["phase"] = "done"


def main(argv=None):
    """Bootstraps a paper-only ConnectionManager and runs run_cycle() every
    CYCLE_SECONDS during market hours. Safety: Settings.validate() (inside
    ConnectionManager's Settings.from_env()) already refuses to construct
    unless trading_mode=='paper' and live_trading_enabled is False -- this
    is enforced upstream of this file, not re-implemented here.

    Loads local-bot.env first (same non-secret config the rest of this
    project's CLI uses via `_settings()` in cli.py) -- without this,
    Settings.from_env() would read a near-empty environment and silently
    fail to find real credentials/paths rather than what's actually
    configured for this machine."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                     help="path to the non-secret bot.env-style config file")
    args = ap.parse_args(argv)
    if Path(args.config).is_file():
        load_env_file(Path(args.config))
    else:
        print(f"WARNING: config file {args.config} not found -- "
              f"relying on whatever is already in the environment")
    settings = Settings.from_env()
    assert settings.trading_mode == "paper" and not settings.live_trading_enabled, (
        "live_paper_runner refuses to start outside paper mode"
    )
    connections = ConnectionManager(settings)
    connections.connect_angel()
    connections.start_background_monitor()
    clock = MarketClock(settings)
    ledger = LiveLedger()
    buffer = LiveBarBuffer()
    seed_con = E.connect()
    buffer.seed(seed_con, date.today())
    seed_con.close()
    print(f"live_paper_runner: ACTIVE_STRATEGIES={ACTIVE_STRATEGIES}, "
          f"seeded {len(buffer.bars)} historical bars for indicator warm-up")
    last_cycle_at = None
    while True:
        now = datetime.now(settings.timezone)
        if clock.entries_allowed(now) or now.time() < dtime(15, 30):
            snap = connections.snapshot()
            if snap.nifty_price:
                buffer.add_tick(now, snap.nifty_price)  # every POLL_SECONDS -- builds real O/H/L/C
            if last_cycle_at is None or (now - last_cycle_at).total_seconds() >= CYCLE_SECONDS:
                try:
                    run_cycle(ledger, buffer, connections, now)
                except Exception as exc:  # keep the loop alive across single-cycle errors
                    print(f"cycle error: {exc}")
                last_cycle_at = now
        time_module.sleep(POLL_SECONDS)


def run_cycle(ledger: LiveLedger, buffer: LiveBarBuffer, connections, now: datetime):
    today = now.date()
    if "s03_orb" in ACTIVE_STRATEGIES:
        run_walk_signal_strategy("s03_orb", s03_orb, s03_orb.resample_5min, ledger, buffer,
                                  connections, today, now)
    if "s04_supertrend_vwap" in ACTIVE_STRATEGIES:
        run_walk_signal_strategy("s04_supertrend_vwap", s04_supertrend_vwap,
                                  s04_supertrend_vwap.resample_5min, ledger, buffer,
                                  connections, today, now)
    if "s07_iron_condor" in ACTIVE_STRATEGIES:
        run_iron_condor(ledger, connections, today, now)
    if "s10_short_straddle_920" in ACTIVE_STRATEGIES:
        m = s10_short_straddle_920
        run_two_leg_stop_strategy(
            "s10_short_straddle_920", ledger, connections, today, now,
            entry_time=dtime(9, 20), strike_step=50.0,
            sl_mult={"CE": m.SL_MULT, "PE": m.SL_MULT}, sl_buffer=m.SL_BUFFER,
            square_off=dtime(15, 6), reentry_time=dtime(12, 30), breakeven=False)
    if "s12_straddle_breakeven_banknifty" in ACTIVE_STRATEGIES:
        m = s12_straddle_breakeven_banknifty
        run_two_leg_stop_strategy(
            "s12_straddle_breakeven_banknifty", ledger, connections, today, now,
            entry_time=dtime(9, 59), strike_step=100.0,
            sl_mult={"CE": m.CE_SL_MULT, "PE": m.PE_SL_MULT}, sl_buffer=0.0,
            square_off=dtime(15, 6), reentry_time=dtime(12, 30), breakeven=True)


if __name__ == "__main__":
    main()
