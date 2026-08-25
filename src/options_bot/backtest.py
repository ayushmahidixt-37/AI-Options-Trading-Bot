"""Offline, read-only replay using only the local market archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import csv
from pathlib import Path

from .candles import Candle
from .config import Settings
from .market_archive import MarketArchive
from .market_events import is_macro_event_window
from .strategy import MomentumStrategy


@dataclass(frozen=True)
class BacktestTrade:
    entered_at: datetime
    exited_at: datetime
    direction: str
    entry: float
    exit: float
    pnl_points: float


def replay_underlying(
    candles: list[Candle], strategy: MomentumStrategy, *, hold_bars: int = 3
) -> list[BacktestTrade]:
    """Replay signals on the bar after each decision to avoid look-ahead."""
    if hold_bars <= 0:
        raise ValueError("hold_bars must be positive")
    trades: list[BacktestTrade] = []
    index = strategy.minimum_candles
    while index + hold_bars < len(candles):
        signal = strategy.evaluate(candles[:index])
        if signal is None:
            index += 1
            continue
        entry_candle = candles[index]
        exit_candle = candles[index + hold_bars]
        multiplier = 1 if signal.direction.value == "bullish" else -1
        trades.append(
            BacktestTrade(
                entered_at=entry_candle.started_at,
                exited_at=exit_candle.started_at,
                direction=signal.direction.value,
                entry=entry_candle.open,
                exit=exit_candle.close,
                pnl_points=(exit_candle.close - entry_candle.open) * multiplier,
            )
        )
        index += hold_bars + 1
    return trades


@dataclass(frozen=True)
class BacktestResult:
    status: str
    trades: int
    winners: int
    losers: int
    gross_pnl_points: float
    win_rate: float
    net_pnl: float
    fees_paid: float
    max_drawdown: float
    profit_factor: float | None
    reason: str
    trade_details: tuple["OptionBacktestTrade", ...] = ()
    trading_days: int = 0
    data_gaps: int = 0

    @property
    def capital_deployed_total(self) -> float:
        """Sum of entry premium (entry_price × units) across all trades.

        Positions are opened one at a time (never overlapping), so this is
        total rupees turned over across the period, not simultaneous margin.
        """
        return round(sum(trade.entry_price * trade.units for trade in self.trade_details), 2)

    @property
    def capital_deployed_average(self) -> float:
        if not self.trade_details:
            return 0.0
        return round(self.capital_deployed_total / len(self.trade_details), 2)

    @property
    def return_on_capital_pct(self) -> float | None:
        if not self.capital_deployed_total:
            return None
        return round(self.net_pnl / self.capital_deployed_total * 100, 2)


@dataclass(frozen=True)
class OptionBacktestTrade:
    signal_at: datetime
    direction: str
    token: str
    symbol: str
    entry_at: datetime
    entry_price: float
    stop_price: float
    exit_at: datetime
    exit_price: float
    exit_reason: str
    units: int
    gross_pnl: float
    fees: float
    net_pnl: float
    raw_points: float


@dataclass(frozen=True)
class BacktestParameters:
    """Explicit, offline-only strategy comparison parameters.

    ``stop_risk_fraction=None`` removes the price-based stop/target/trailing
    exit entirely, so a trade only closes on a signal reversal, a max-hold
    cap, or the session's force-exit time -- useful for seeing a strategy's
    un-stopped behaviour before deciding what stop distance actually fits
    the instrument.

    ``minimum_option_premium`` skips a trade whose selected contract's entry
    price is below this. Found 2026-08-22 in a per-trade loss analysis: the
    points-based stop distance (a fixed rupee budget divided by lot size)
    can exceed a cheap option's entire premium, so the stop mathematically
    cannot fire before the option is worthless -- these trades instead ride
    to signal-reversal or force-exit, sometimes losing money even when the
    price moved favorably, because the flat per-order fee alone exceeds the
    tiny absolute gain available. See BACKTEST_FINDINGS.md's 2026-08-22 loss
    post-mortem entry.

    ``exclude_macro_event_days`` skips a signal observed on (or the trading
    day after) a known scheduled macro event -- RBI MPC / FOMC rate
    decisions, the Union Budget -- per ``market_events.py``. Deliberately
    limited to *scheduled* events with a public date, not a news/sentiment
    feed; see that module's docstring for why.

    ``minimum_signal_confidence`` skips a trade whose originating signal's
    own confidence score (0.5-0.95, computed per-strategy at signal time --
    see each strategy's ``evaluate``/``signal_from_indicators``) is below
    this. Uses data every strategy already computes and discards after
    generating the trade; listed as an untested breakdown dimension in
    BACKTEST_FINDINGS.md before being tried here.

    ``minimum_open_interest`` skips a trade whose selected contract's most
    recent known open interest (as of signal time, from the archived option
    candles) is below this, or whose open interest is unknown entirely.
    Only meaningful for ``run_upstox_backtest``, which is the only engine
    with per-contract OI available before a trade is built.

    ``minimum_ema_separation`` skips a trade whose originating signal's fast/
    slow EMA gap (normalized by price, i.e. ``abs(fast-slow)/close``) is below
    this -- trend *strength*, not just direction. Only available for
    strategies that expose ``signal_from_indicators``/``_with_macro`` to
    ``upstox_backtest.generate_signals_from_candles`` (a bare direction
    crossover doesn't distinguish a 0.1-point flip from a 5-point one).

    ``minimum_opening_range_pct`` skips a trade whose signal day's opening
    range (the underlying's high-low over the first ``opening_range_bars``
    candles) is narrower than this fraction of spot -- the flip side of the
    filter built for the short-strangle engine (there, a *narrow* opening
    range selects calm days worth selling premium on; here, a *wide* one is
    tested as a possible signal for days worth trading trend/breakout
    strategies on). No lookahead: a signal observed before the opening
    range has actually finished forming is skipped outright (fails closed),
    since the range's width genuinely isn't knowable yet at that point --
    it is not simply assumed narrow or evaluated against a partial range.

    ``trailing_activation_return`` delays ``trailing_stop`` from ratcheting
    until the position has actually reached this unrealized return -- before
    that, only the fixed initial stop applies. Without this, trailing_stop
    starts tightening from the very first candle (peak_price starts at the
    entry fill), which can clip a winner on ordinary early noise before it
    has proven itself. Combine with ``target_return=None`` for a "let it run,
    protect the gain once it's real" exit instead of a hard profit cap --
    the position is never forced out at a fixed target, only once it pulls
    back from its own peak by ``trailing_stop`` after clearing the
    activation threshold.

    ``minimum_implied_volatility``/``maximum_implied_volatility`` skip a
    trade whose selected contract's implied volatility (as of the entry
    candle, from DhanHQ's historical IV -- see ``dhan_ingest.py``'s
    ``backfill_iv_for_weekly_cycle``) falls outside this range. A contract
    with IV recorded as exactly ``0`` is treated as unknown (fails closed,
    not as "very low IV") -- Dhan's own historical feed returns 0 for
    illiquid/edge-case moments it apparently couldn't price, about 6.4% of
    all backfilled rows; see BACKTEST_FINDINGS.md's 2026-08-24 IV entry for
    the data-quality check performed before trusting this field. Only
    meaningful for ``run_upstox_backtest`` with ``include_dhan=True`` --
    Upstox-sourced candles never populate this column.
    """

    name: str = "Baseline"
    bullish_rsi_min: float | None = None
    bearish_rsi_max: float | None = None
    minimum_atr: float | None = None
    entry_start: time | None = None
    entry_end: time | None = None
    exclude_expiry_day: bool = False
    stop_risk_fraction: float | None = 0.8
    maximum_hold_minutes: int | None = None
    target_return: float | None = None
    trailing_stop: float | None = None
    minimum_option_premium: float | None = None
    exclude_macro_event_days: bool = False
    allowed_weekdays: tuple[int, ...] | None = None
    minimum_signal_confidence: float | None = None
    minimum_open_interest: float | None = None
    minimum_ema_separation: float | None = None
    trailing_activation_return: float | None = None
    minimum_opening_range_pct: float | None = None
    opening_range_bars: int = 6
    minimum_implied_volatility: float | None = None
    maximum_implied_volatility: float | None = None


def run_momentum_backtest(
    archive: MarketArchive,
    start: date | None = None,
    end: date | None = None,
    settings: Settings | None = None,
    parameters: BacktestParameters | None = None,
) -> BacktestResult:
    """Replay archived signals and option candles without network or order calls."""
    clauses: list[str] = []
    sql_parameters: list[str] = []
    if start:
        clauses.append("date(observed_at)>=?")
        sql_parameters.append(start.isoformat())
    if end:
        clauses.append("date(observed_at)<=?")
        sql_parameters.append(end.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with archive.connect() as con:
        observations = con.execute(
            f"""SELECT observed_at, spot, signal, rsi, atr FROM strategy_observations
                {where} AND signal IN ('BULLISH','BEARISH')
                ORDER BY observed_at"""
            if where
            else """SELECT observed_at, spot, signal, rsi, atr FROM strategy_observations
                     WHERE signal IN ('BULLISH','BEARISH') ORDER BY observed_at""",
            sql_parameters,
        ).fetchall()
        variant = parameters or BacktestParameters()
        observations = [
            row
            for row in observations
            if _observation_allowed(row, variant)
        ]
        trading_days = int(
            con.execute(
                f"SELECT COUNT(DISTINCT date(observed_at)) FROM strategy_observations {where}",
                sql_parameters,
            ).fetchone()[0]
        )
        observations = [
            row
            for index, row in enumerate(observations)
            if index == 0 or row[2] != observations[index - 1][2]
        ]
        trades: list[OptionBacktestTrade] = []
        for index, observation in enumerate(observations):
            observed_at = datetime.fromisoformat(observation[0])
            option_type = "CE" if observation[2] == "BULLISH" else "PE"
            contract = con.execute(
                """SELECT token, lot_size, symbol, expiry FROM instruments
                   WHERE underlying='NIFTY' AND option_type=? AND expiry>=date(?)
                   ORDER BY expiry, ABS(strike-?) LIMIT 1""",
                (option_type, observed_at.isoformat(), observation[1]),
            ).fetchone()
            if contract is None:
                continue
            if variant.exclude_expiry_day and contract[3] == observed_at.date().isoformat():
                continue
            entry = con.execute(
                """SELECT started_at, open FROM market_candles
                   WHERE instrument_token=? AND started_at>? AND date(started_at)=?
                   ORDER BY started_at LIMIT 1""",
                (contract[0], observed_at.isoformat(), observed_at.date().isoformat()),
            ).fetchone()
            if entry is None:
                continue
            force_exit = settings.force_exit if settings else time(15, 20)
            session_exit = datetime.combine(
                observed_at.date(), force_exit, tzinfo=observed_at.tzinfo
            ).isoformat()
            next_signal = (
                observations[index + 1][0]
                if index + 1 < len(observations)
                else session_exit
            )
            next_observed = min(next_signal, session_exit)
            timed_exit = False
            if variant.maximum_hold_minutes:
                hold_exit = observed_at + timedelta(minutes=variant.maximum_hold_minutes)
                timed_exit = hold_exit.isoformat() < next_observed
                next_observed = min(next_observed, hold_exit.isoformat())
            path = con.execute(
                """SELECT started_at, open, high, low, close FROM market_candles
                   WHERE instrument_token=? AND started_at>=? AND started_at<=?
                   ORDER BY started_at""",
                (contract[0], entry[0], next_observed),
            ).fetchall()
            if not path:
                continue
            slippage = settings.paper_slippage_bps / 10_000 if settings else 0.0
            buy_fill = round(float(entry[1]) * (1 + slippage), 2)
            units = int(contract[1])
            fees = 2 * settings.paper_fee_per_order if settings else 0.0
            has_stop_cap = settings and variant.stop_risk_fraction is not None
            risk_budget = (
                settings.max_loss_per_trade * variant.stop_risk_fraction
                if has_stop_cap
                else float("inf")
            )
            stop_distance = (risk_budget - fees) / units if has_stop_cap else float("inf")
            stop = round(buy_fill - stop_distance, 2) if has_stop_cap else 0.0
            selected_exit = path[-1]
            exit_price = float(selected_exit[4])
            exit_reason = "max-hold" if timed_exit else (
                "signal-reversal" if next_signal < session_exit else "force-exit"
            )
            if settings and stop > 0:
                active_stop = stop
                peak_price = buy_fill
                for candle in path:
                    if float(candle[1]) <= active_stop:
                        selected_exit, exit_price, exit_reason = (
                            candle,
                            float(candle[1]),
                            "stop-gap",
                        )
                        break
                    if float(candle[3]) <= active_stop:
                        selected_exit, exit_price, exit_reason = candle, active_stop, "stop"
                        break
                    target = (
                        buy_fill * (1 + variant.target_return)
                        if variant.target_return
                        else None
                    )
                    if target and float(candle[2]) >= target:
                        selected_exit, exit_price, exit_reason = candle, target, "target"
                        break
                    peak_price = max(peak_price, float(candle[2]))
                    if variant.trailing_stop:
                        active_stop = max(
                            active_stop, peak_price * (1 - variant.trailing_stop)
                        )
            sell_fill = round(exit_price * (1 - slippage), 2)
            gross = round((sell_fill - buy_fill) * units, 2)
            net = round(gross - fees, 2)
            trades.append(
                OptionBacktestTrade(
                    signal_at=observed_at,
                    direction=str(observation[2]),
                    token=str(contract[0]),
                    symbol=str(contract[2]),
                    entry_at=datetime.fromisoformat(entry[0]),
                    entry_price=buy_fill,
                    stop_price=stop,
                    exit_at=datetime.fromisoformat(selected_exit[0]),
                    exit_price=sell_fill,
                    exit_reason=exit_reason,
                    units=units,
                    gross_pnl=gross,
                    fees=fees,
                    net_pnl=net,
                    raw_points=round(exit_price - float(entry[1]), 2),
                )
            )

    return build_backtest_result(
        trades, archive, settings, trading_days, source="angel-one", start=start, end=end
    )


def build_backtest_result(
    trades: list[OptionBacktestTrade],
    archive: MarketArchive,
    settings: Settings | None,
    trading_days: int,
    source: str = "angel-one",
    start: date | None = None,
    end: date | None = None,
) -> BacktestResult:
    """Aggregate trades into a ``BacktestResult``, shared by every replay engine.

    ``source`` scopes the data-quality gap check to the engine's own data
    (``"angel-one"`` or ``"upstox"``) so a gap in one source never marks the
    other source's backtest status as impaired. ``start``/``end`` scope it
    further to the range actually being backtested -- without this the gap
    count (and therefore ``DATA QUALITY WARNING``) reflects the whole
    archive, not the requested range; see ``gap_summary``'s docstring.
    """
    if not trades:
        return BacktestResult(
            "INSUFFICIENT DATA",
            0,
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            None,
            "Collect signal observations and matching option candles before backtesting.",
        )
    winners = sum(item.net_pnl > 0 for item in trades)
    net_values = [item.net_pnl for item in trades]
    losers = sum(value <= 0 for value in net_values)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in net_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gains = sum(value for value in net_values if value > 0)
    losses = abs(sum(value for value in net_values if value < 0))
    profit_factor = gains / losses if losses else None
    fees_paid = len(trades) * 2 * settings.paper_fee_per_order if settings else 0.0
    gaps = sum(int(item["gaps"]) for item in archive.gap_summary(source, start, end))
    status = (
        "VALIDATION READY"
        if trading_days >= 20 and len(trades) >= 30 and gaps == 0
        else ("DATA QUALITY WARNING" if gaps else "PRELIMINARY")
    )
    return BacktestResult(
        status,
        len(trades),
        winners,
        losers,
        sum(item.raw_points for item in trades),
        winners / len(trades),
        sum(net_values),
        fees_paid,
        max_drawdown,
        profit_factor,
        "Entry uses the next archived option candle open; results apply configured lot size, fees, and slippage.",
        tuple(trades),
        trading_days,
        gaps,
    )


def _observation_allowed(row: object, parameters: BacktestParameters) -> bool:
    observed_at = datetime.fromisoformat(row[0])
    signal = str(row[2])
    rsi_value = float(row[3]) if row[3] is not None else None
    atr_value = float(row[4]) if row[4] is not None else None
    if parameters.entry_start and observed_at.time() < parameters.entry_start:
        return False
    if (
        parameters.allowed_weekdays is not None
        and observed_at.weekday() not in parameters.allowed_weekdays
    ):
        return False
    if parameters.entry_end and observed_at.time() > parameters.entry_end:
        return False
    if parameters.minimum_atr is not None and (
        atr_value is None or atr_value < parameters.minimum_atr
    ):
        return False
    if parameters.exclude_macro_event_days and is_macro_event_window(observed_at.date()):
        return False
    if signal == "BULLISH" and parameters.bullish_rsi_min is not None and (
        rsi_value is None or rsi_value < parameters.bullish_rsi_min
    ):
        return False
    return not (
        signal == "BEARISH"
        and parameters.bearish_rsi_max is not None
        and (rsi_value is None or rsi_value > parameters.bearish_rsi_max)
    )


def export_backtest_csv(result: BacktestResult, target: str | Path) -> Path:
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "signal_at",
                "direction",
                "symbol",
                "entry_at",
                "entry_price",
                "stop_price",
                "exit_at",
                "exit_price",
                "exit_reason",
                "units",
                "gross_pnl",
                "fees",
                "net_pnl",
            )
        )
        for trade in result.trade_details:
            writer.writerow(
                (
                    trade.signal_at.isoformat(),
                    trade.direction,
                    trade.symbol,
                    trade.entry_at.isoformat(),
                    trade.entry_price,
                    trade.stop_price,
                    trade.exit_at.isoformat(),
                    trade.exit_price,
                    trade.exit_reason,
                    trade.units,
                    trade.gross_pnl,
                    trade.fees,
                    trade.net_pnl,
                )
            )
    return destination
