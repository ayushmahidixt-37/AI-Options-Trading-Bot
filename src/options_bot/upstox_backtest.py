"""Offline replay of Upstox-sourced candles, with no strategy_observations dependency.

This module is deliberately separate from ``backtest.py``'s in-production,
Angel-observation-based replay so that path is never touched by this
read-only, backtesting-only feature. It shares the same trade construction
discipline (chronological, no look-ahead, conservative fills) and the same
result aggregation (``build_backtest_result``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .backtest import (
    BacktestParameters,
    BacktestResult,
    OptionBacktestTrade,
    _observation_allowed,
    build_backtest_result,
)
from .candles import Candle
from .config import Settings
from .indicators import rsi as compute_rsi
from .market_archive import MarketArchive
from .strategy import MomentumStrategy
from .upstox_ingest import NIFTY_UNDERLYING_KEY


@dataclass(frozen=True)
class SyntheticObservation:
    """One in-memory, walk-forward signal derived from raw Upstox candles."""

    observed_at: datetime
    spot: float
    signal: str
    rsi: float | None
    atr: float
    confidence: float


def generate_signals_from_candles(
    candles: list[Candle], strategy: MomentumStrategy
) -> list[SyntheticObservation]:
    """Walk candles forward, evaluating only on data known at each point.

    Mirrors ``replay_underlying``'s ``candles[:index]`` slicing so no signal
    ever sees a future candle, and only emits when the direction changes from
    the last emitted signal (matching the live monitor's fresh-signal
    deduplication convention).
    """
    observations: list[SyntheticObservation] = []
    last_signal: str | None = None
    for index in range(strategy.minimum_candles, len(candles)):
        window = candles[:index]
        signal = strategy.evaluate(window)
        if signal is None:
            continue
        label = signal.direction.value.upper()
        if label == last_signal:
            continue
        last_signal = label
        closes = [item.close for item in window]
        rsi_series = compute_rsi(closes, strategy.rsi_period)
        rsi_value = rsi_series[-1] if rsi_series else None
        decision_candle = window[-1]
        observations.append(
            SyntheticObservation(
                observed_at=decision_candle.started_at,
                spot=decision_candle.close,
                signal=label,
                rsi=rsi_value,
                atr=signal.stop_distance,
                confidence=signal.confidence,
            )
        )
    return observations


def run_upstox_backtest(
    archive: MarketArchive,
    strategy: MomentumStrategy | None = None,
    start: date | None = None,
    end: date | None = None,
    settings: Settings | None = None,
    parameters: BacktestParameters | None = None,
    underlying_key: str = NIFTY_UNDERLYING_KEY,
    timeframe: str = "FIVE_MINUTE",
) -> BacktestResult:
    """Replay Upstox-sourced underlying and option candles for backtesting.

    Only reads ``market_candles``/``instruments`` rows tagged ``source='upstox'``
    — Angel-sourced data in the same archive is never touched or mixed in.
    """
    strategy = strategy or MomentumStrategy()
    variant = parameters or BacktestParameters()
    with archive.connect() as con:
        clauses = ["instrument_token=?", "source='upstox'", "timeframe=?"]
        sql_parameters: list[object] = [underlying_key, timeframe]
        if start:
            clauses.append("date(started_at)>=?")
            sql_parameters.append(start.isoformat())
        if end:
            clauses.append("date(started_at)<=?")
            sql_parameters.append(end.isoformat())
        where = " AND ".join(clauses)
        rows = con.execute(
            f"""SELECT started_at, symbol, open, high, low, close FROM market_candles
                WHERE {where} ORDER BY started_at""",
            sql_parameters,
        ).fetchall()
        underlying_candles = [
            Candle(
                symbol=str(row[1]),
                started_at=datetime.fromisoformat(row[0]),
                open=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                close=float(row[5]),
            )
            for row in rows
        ]
        trading_days = len({candle.started_at.date() for candle in underlying_candles})

        raw_observations = generate_signals_from_candles(underlying_candles, strategy)
        observations = [
            observation
            for observation in raw_observations
            if _observation_allowed(
                (
                    observation.observed_at.isoformat(),
                    observation.spot,
                    observation.signal,
                    observation.rsi,
                    observation.atr,
                ),
                variant,
            )
        ]

        trades: list[OptionBacktestTrade] = []
        for index, observation in enumerate(observations):
            observed_at = observation.observed_at
            option_type = "CE" if observation.signal == "BULLISH" else "PE"
            contract = con.execute(
                """SELECT i.token, i.lot_size, i.symbol, i.expiry
                   FROM instruments i
                   WHERE i.underlying='NIFTY' AND i.option_type=? AND i.expiry>=date(?)
                     AND i.token IN (
                         SELECT DISTINCT instrument_token FROM market_candles WHERE source='upstox'
                     )
                   ORDER BY i.expiry, ABS(i.strike-?) LIMIT 1""",
                (option_type, observed_at.isoformat(), observation.spot),
            ).fetchone()
            if contract is None:
                continue
            if variant.exclude_expiry_day and contract[3] == observed_at.date().isoformat():
                continue
            entry = con.execute(
                """SELECT started_at, open FROM market_candles
                   WHERE instrument_token=? AND source='upstox' AND started_at>? AND date(started_at)=?
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
                observations[index + 1].observed_at.isoformat()
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
                   WHERE instrument_token=? AND source='upstox' AND started_at>=? AND started_at<=?
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
                    direction=observation.signal,
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
    return build_backtest_result(trades, archive, settings, trading_days, source="upstox")
