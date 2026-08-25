"""Offline backtest engine for short-premium (non-directional) strategies
-- selling a strangle instead of buying a single-leg option.

Every other strategy in this project buys options: the max loss is the
premium paid, known and capped upfront, and the position profits when the
underlying makes a large enough directional move. A short strangle is the
opposite kind of bet -- it profits from the underlying *not* moving much
(collecting time decay on both a call and a put sold out-of-the-money),
and its max loss is theoretically unbounded until the position is closed,
not capped by anything paid upfront. This module exists to test that idea
honestly, with the same paper-only, backtest-only discipline as every
other strategy here -- it never places a real order and does not change
this project's live-trading safety boundary in any way.

Deliberately simpler than ``upstox_backtest.py``'s intrabar high/low
peeking: this walks both legs' candles close-to-close rather than checking
each leg's high/low independently, because a short strangle's two legs
rarely hit their own worst extreme at exactly the same instant -- summing
independent intrabar highs would overstate how bad the combined position
could plausibly get. Close-to-close mark-to-market is a more conservative,
defensible first version; intrabar peeking (with the two legs correlated
somehow) would need real justification, not just symmetry with the
long-only engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from . import short_strangle_ml_features
from .config import Settings
from .market_archive import MarketArchive
from .ml_model import SignalQualityModel
from .upstox_ingest import NIFTY_UNDERLYING_KEY

DEFAULT_ENTRY_TIME = time(9, 45)
DEFAULT_FORCE_EXIT = time(15, 20)


@dataclass(frozen=True)
class ShortStrangleParameters:
    """Explicit, offline-only short-strangle configuration.

    ``strike_distance_pct`` is how far out-of-the-money each leg is sold,
    as a fraction of spot (e.g. 0.01 = sell the call nearest 1% above spot
    and the put nearest 1% below).

    ``stop_multiple`` closes the position at a loss once the combined cost
    to buy both legs back reaches this multiple of the premium originally
    collected (e.g. 1.5 = close once it would cost 1.5x what was collected
    to exit). ``target_fraction`` closes it at a profit once that cost
    decays to this fraction of the premium collected (e.g. 0.5 = close
    once 50% of the credit has decayed away). Both are evaluated close-to-
    close; see the module docstring for why.

    ``exclude_expiry_day`` skips entering a fresh position on the
    underlying's own expiry day -- gamma risk near expiry is the sharpest
    for a short position, and this project's long-only strategies already
    treat expiry day as worth excluding by default in some configurations.

    ``maximum_opening_range_pct`` skips entering on a day whose opening
    range (the underlying's high-low over the first ``opening_range_bars``
    candles) is wider than this fraction of spot -- a same-day, no-lookahead
    proxy for "does today look calm enough to sell premium" (the entry
    itself only happens at/after ``entry_time``, by which point the opening
    range has already closed). Added 2026-08-23 after backtesting this
    strategy unconditionally (every day, regardless of conditions) found no
    stable edge across 7 quarters -- the fix is deploying it selectively,
    not abandoning the idea; see BACKTEST_FINDINGS.md's 2026-08-23 entries.
    """

    name: str = "ShortStrangle"
    strike_distance_pct: float = 0.01
    entry_time: time = DEFAULT_ENTRY_TIME
    stop_multiple: float = 1.5
    target_fraction: float = 0.5
    exclude_expiry_day: bool = True
    allowed_weekdays: tuple[int, ...] | None = None
    maximum_opening_range_pct: float | None = None
    opening_range_bars: int = 6


@dataclass(frozen=True)
class ShortStrangleTrade:
    entry_at: datetime
    exit_at: datetime
    call_token: str
    call_symbol: str
    call_strike: float
    call_entry_price: float
    call_exit_price: float
    put_token: str
    put_symbol: str
    put_strike: float
    put_entry_price: float
    put_exit_price: float
    units: int
    gross_pnl: float
    fees: float
    net_pnl: float
    exit_reason: str

    @property
    def premium_collected(self) -> float:
        return round((self.call_entry_price + self.put_entry_price) * self.units, 2)


@dataclass(frozen=True)
class ShortPremiumResult:
    status: str
    trades: int
    winners: int
    losers: int
    win_rate: float
    net_pnl: float
    fees_paid: float
    max_drawdown: float
    profit_factor: float | None
    reason: str
    trade_details: tuple[ShortStrangleTrade, ...] = field(default_factory=tuple)
    trading_days: int = 0

    @property
    def premium_collected_total(self) -> float:
        """Sum of premium received across all trades -- NOT the same thing
        as capital deployed on a long position. This project's long-only
        strategies pay this amount upfront; a short strangle receives it,
        and real exchange margin requirements for holding the short
        position are typically several times larger than the premium
        collected (SPAN + exposure margin), which this backtest does not
        model. Do not compare return_on_premium_pct directly against the
        long-only candidates' return_on_capital_pct -- they are returns on
        two different, non-comparable bases."""
        return round(sum(t.premium_collected for t in self.trade_details), 2)

    @property
    def return_on_premium_pct(self) -> float | None:
        if not self.premium_collected_total:
            return None
        return round(self.net_pnl / self.premium_collected_total * 100, 2)


def run_short_strangle_backtest(
    archive: MarketArchive,
    start: date | None = None,
    end: date | None = None,
    settings: Settings | None = None,
    parameters: ShortStrangleParameters | None = None,
    underlying_key: str = NIFTY_UNDERLYING_KEY,
    timeframe: str = "FIVE_MINUTE",
    include_derived: bool = False,
    include_dhan: bool = False,
    dhan_only: bool = False,
    ml_model: SignalQualityModel | None = None,
) -> ShortPremiumResult:
    """Replay a daily short-strangle over archived Upstox candles.

    One entry attempt per trading day, at the first candle at or after
    ``parameters.entry_time``. Both legs must have archived option candle
    data at matching timestamps to be walked forward together -- a
    timestamp only one leg has data for is skipped (fail-closed, not an
    approximation of the missing leg's price).

    ``include_dhan``/``dhan_only`` mirror ``run_upstox_backtest``'s
    parameters of the same name -- see that function's docstring for the
    full rationale (added 2026-08-25 to extend this engine to the same
    2020-2024 DhanHQ-backed range Candidate B and ORB were confirmed
    against). ``dhan_only`` scopes to the option legs only; the underlying
    query is unaffected since it's stored under one shared token regardless
    of source.

    ``ml_model`` (added 2026-08-25), if given, *replaces*
    ``variant.maximum_opening_range_pct`` as the day's entry gate --
    ``short_strangle_ml_features.FEATURE_NAMES`` includes
    ``opening_range_pct`` itself, so the model subsumes that hard cutoff
    rather than stacking with it. See ``research/train_short_strangle_ml_model.py``
    for how a model is trained; passing ``ml_model=None`` (the default)
    leaves every existing caller's behavior completely unchanged.
    """
    variant = parameters or ShortStrangleParameters()
    derived_filter = "" if include_derived else " AND derived_from_timeframe IS NULL"
    source_clause = "source IN ('upstox','dhan')" if include_dhan else "source='upstox'"
    option_source_clause = "source='dhan'" if dhan_only else source_clause
    with archive.connect() as con:
        clauses = ["instrument_token=?", source_clause, "timeframe=?"]
        sql_parameters: list[object] = [underlying_key, timeframe]
        if not include_derived:
            clauses.append("derived_from_timeframe IS NULL")
        if start:
            clauses.append("date(started_at)>=?")
            sql_parameters.append(start.isoformat())
        if end:
            clauses.append("date(started_at)<=?")
            sql_parameters.append(end.isoformat())
        where = " AND ".join(clauses)
        rows = con.execute(
            f"""SELECT started_at, close, high, low FROM market_candles
                WHERE {where} ORDER BY started_at""",
            sql_parameters,
        ).fetchall()
        if not rows:
            return ShortPremiumResult(
                "INSUFFICIENT DATA", 0, 0, 0, 0.0, 0.0, 0.0, 0.0, None,
                "Collect underlying candles before backtesting.",
            )

        by_day: dict[str, list[tuple[str, float, float, float]]] = {}
        for started_at, close, high, low in rows:
            day = started_at[:10]
            by_day.setdefault(day, []).append((started_at, float(close), float(high), float(low)))
        trading_days = len(by_day)

        con.execute("DROP TABLE IF EXISTS temp.available_upstox_tokens")
        con.execute(
            f"""CREATE TEMP TABLE available_upstox_tokens AS
                SELECT DISTINCT instrument_token FROM market_candles
                WHERE {option_source_clause}{derived_filter}"""
        )
        con.execute(
            "CREATE INDEX temp.available_upstox_tokens_idx ON available_upstox_tokens(instrument_token)"
        )

        force_exit = settings.force_exit if settings else DEFAULT_FORCE_EXIT
        slippage = settings.paper_slippage_bps / 10_000 if settings else 0.0
        fee = settings.paper_fee_per_order if settings else 0.0
        trades: list[ShortStrangleTrade] = []
        # Causal, day-by-day rolling state for short_strangle_ml_features --
        # updated once per calendar day regardless of whether a trade is
        # taken that day, using only closes strictly before today, so the
        # ML path below never sees data from its own decision day.
        prior_close: float | None = None
        trailing_daily_returns: list[float] = []

        for day, day_rows in sorted(by_day.items()):
            day_rows.sort()
            day_close = day_rows[-1][1]
            previous_close = prior_close
            if previous_close is not None:
                trailing_daily_returns.append((day_close - previous_close) / previous_close)
            prior_close = day_close

            entry_row = next(
                (row for row in day_rows if datetime.fromisoformat(row[0]).time() >= variant.entry_time),
                None,
            )
            if entry_row is None:
                continue
            entry_at = datetime.fromisoformat(entry_row[0])
            if variant.allowed_weekdays and entry_at.weekday() not in variant.allowed_weekdays:
                continue
            spot = entry_row[1]

            if ml_model is not None:
                # Replaces maximum_opening_range_pct entirely -- see the
                # docstring above. opening_range_pct is itself one of the
                # model's features, so this is a strict generalization of
                # the hard cutoff, not a second, stacked filter.
                opening_bars = day_rows[: variant.opening_range_bars]
                if len(opening_bars) < variant.opening_range_bars:
                    continue
                range_high = max(r[2] for r in opening_bars)
                range_low = min(r[3] for r in opening_bars)
                if range_low <= 0:
                    continue
                expiry_row = con.execute(
                    "SELECT MIN(expiry) FROM instruments WHERE underlying='NIFTY' AND expiry>=date(?)",
                    (day,),
                ).fetchone()
                if not expiry_row or not expiry_row[0]:
                    continue
                days_to_expiry = (date.fromisoformat(expiry_row[0]) - date.fromisoformat(day)).days
                features = short_strangle_ml_features.extract_features(
                    entry_day=date.fromisoformat(day),
                    range_high=range_high,
                    range_low=range_low,
                    days_to_expiry=days_to_expiry,
                    prior_close=previous_close,
                    entry_spot=spot,
                    trailing_daily_returns=trailing_daily_returns,
                )
                if not ml_model.decide(features):
                    continue
            elif variant.maximum_opening_range_pct is not None:
                opening_bars = day_rows[: variant.opening_range_bars]
                if len(opening_bars) < variant.opening_range_bars:
                    continue  # not enough of the day archived to judge the opening range
                range_high = max(r[2] for r in opening_bars)
                range_low = min(r[3] for r in opening_bars)
                if range_low <= 0 or (range_high - range_low) / range_low > variant.maximum_opening_range_pct:
                    continue  # today's opening move was too wide -- skip selling premium

            call_contract = con.execute(
                """SELECT i.token, i.lot_size, i.symbol, i.expiry FROM instruments i
                   WHERE i.underlying='NIFTY' AND i.option_type='CE' AND i.expiry>=date(?)
                     AND i.token IN (SELECT instrument_token FROM available_upstox_tokens)
                     AND i.strike>=?
                   ORDER BY i.expiry, i.strike LIMIT 1""",
                (day, spot * (1 + variant.strike_distance_pct)),
            ).fetchone()
            put_contract = con.execute(
                """SELECT i.token, i.lot_size, i.symbol, i.expiry FROM instruments i
                   WHERE i.underlying='NIFTY' AND i.option_type='PE' AND i.expiry>=date(?)
                     AND i.token IN (SELECT instrument_token FROM available_upstox_tokens)
                     AND i.strike<=?
                   ORDER BY i.expiry, i.strike DESC LIMIT 1""",
                (day, spot * (1 - variant.strike_distance_pct)),
            ).fetchone()
            if call_contract is None or put_contract is None:
                continue
            if variant.exclude_expiry_day and (call_contract[3] == day or put_contract[3] == day):
                continue
            if call_contract[3] != put_contract[3]:
                continue  # only trade a strangle with both legs on the same expiry

            session_exit = datetime.combine(entry_at.date(), force_exit, tzinfo=entry_at.tzinfo).isoformat()
            call_rows = {
                r[0]: float(r[1]) for r in con.execute(
                    f"""SELECT started_at, close FROM market_candles
                       WHERE instrument_token=? AND {option_source_clause} AND timeframe=?{derived_filter}
                         AND started_at>=? AND started_at<=? ORDER BY started_at""",
                    (call_contract[0], timeframe, entry_at.isoformat(), session_exit),
                ).fetchall()
            }
            put_rows = {
                r[0]: float(r[1]) for r in con.execute(
                    f"""SELECT started_at, close FROM market_candles
                       WHERE instrument_token=? AND {option_source_clause} AND timeframe=?{derived_filter}
                         AND started_at>=? AND started_at<=? ORDER BY started_at""",
                    (put_contract[0], timeframe, entry_at.isoformat(), session_exit),
                ).fetchall()
            }
            joint_timestamps = sorted(set(call_rows) & set(put_rows))
            if not joint_timestamps:
                continue

            call_entry_close = call_rows[joint_timestamps[0]]
            put_entry_close = put_rows[joint_timestamps[0]]
            call_entry_fill = round(call_entry_close * (1 - slippage), 2)
            put_entry_fill = round(put_entry_close * (1 - slippage), 2)
            premium_collected = call_entry_fill + put_entry_fill
            units = int(call_contract[1])

            exit_ts = joint_timestamps[-1]
            exit_reason = "force-exit"
            for ts in joint_timestamps[1:]:
                cost_to_close = call_rows[ts] + put_rows[ts]
                if cost_to_close >= premium_collected * variant.stop_multiple:
                    exit_ts, exit_reason = ts, "stop"
                    break
                if cost_to_close <= premium_collected * variant.target_fraction:
                    exit_ts, exit_reason = ts, "target"
                    break

            call_exit_fill = round(call_rows[exit_ts] * (1 + slippage), 2)
            put_exit_fill = round(put_rows[exit_ts] * (1 + slippage), 2)
            gross = round((premium_collected - (call_exit_fill + put_exit_fill)) * units, 2)
            fees = 4 * fee
            net = round(gross - fees, 2)

            trades.append(
                ShortStrangleTrade(
                    entry_at=entry_at,
                    exit_at=datetime.fromisoformat(exit_ts),
                    call_token=str(call_contract[0]), call_symbol=str(call_contract[2]),
                    call_strike=float(spot * (1 + variant.strike_distance_pct)),
                    call_entry_price=call_entry_fill, call_exit_price=call_exit_fill,
                    put_token=str(put_contract[0]), put_symbol=str(put_contract[2]),
                    put_strike=float(spot * (1 - variant.strike_distance_pct)),
                    put_entry_price=put_entry_fill, put_exit_price=put_exit_fill,
                    units=units, gross_pnl=gross, fees=fees, net_pnl=net, exit_reason=exit_reason,
                )
            )

    if not trades:
        return ShortPremiumResult(
            "INSUFFICIENT DATA", 0, 0, 0, 0.0, 0.0, 0.0, 0.0, None,
            "No short-strangle trades matched both legs' data.", trading_days=trading_days,
        )

    winners = sum(1 for t in trades if t.net_pnl > 0)
    losers = len(trades) - winners
    net_values = [t.net_pnl for t in trades]
    equity = peak = max_drawdown = 0.0
    for value in net_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gains = sum(v for v in net_values if v > 0)
    losses = -sum(v for v in net_values if v < 0)
    profit_factor = round(gains / losses, 4) if losses else None
    return ShortPremiumResult(
        status="OK",
        trades=len(trades),
        winners=winners,
        losers=losers,
        win_rate=round(winners / len(trades), 4),
        net_pnl=round(sum(net_values), 2),
        fees_paid=round(sum(t.fees for t in trades), 2),
        max_drawdown=round(max_drawdown, 2),
        profit_factor=profit_factor,
        reason="",
        trade_details=tuple(trades),
        trading_days=trading_days,
    )
