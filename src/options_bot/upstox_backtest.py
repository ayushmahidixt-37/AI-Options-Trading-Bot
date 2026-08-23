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
from .indicators import atr_series, ema
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
    ema_gap_normalized: float | None = None


def generate_signals_from_candles(
    candles: list[Candle], strategy: MomentumStrategy
) -> list[SyntheticObservation]:
    """Walk candles forward, evaluating only on data known at each point.

    No signal ever sees a future candle: at step ``index``, only
    ``candles[:index]`` (i.e. everything up to and including
    ``candles[index-1]``) has been used. Only emits when the direction
    changes from the last emitted signal (matching the live monitor's
    fresh-signal deduplication convention).

    Every indicator series is computed once, in one linear pass, rather than
    recomputing EMA/RSI/ATR from scratch over an ever-growing prefix on
    every step -- the latter is quadratic in the number of candles and
    became a real, multi-hour bottleneck once the archive grew large (found
    2026-08-22 training the ML filter on the extended history). ``ema``/
    ``rsi`` are already pure left-to-right recursions, so ``series[k]`` is
    exactly what ``strategy.evaluate`` would compute from ``candles[:k+1]``
    -- computing them once for the whole array and indexing in is provably
    identical, not an approximation; see ``indicators.atr_series``'s
    docstring for the same argument applied to ATR (previously only
    available as a recompute-from-scratch scalar).

    Only used for a strategy that exposes ``signal_from_indicators`` (real
    ``MomentumStrategy``, whose period attributes this needs to compute the
    series) or ``signal_from_indicators_with_macro`` (same idea, plus one
    extra macro-trend EMA series, for ``TrendConfirmedMomentumStrategy``).
    Anything else -- a test double scripting ``evaluate`` directly, for
    instance -- falls back to the original, slower per-step call, which is
    fine at test scale and keeps this optimization from ever silently
    changing what a custom strategy sees.
    """
    observations: list[SyntheticObservation] = []
    last_signal: str | None = None
    if len(candles) <= strategy.minimum_candles:
        return observations

    if hasattr(strategy, "signal_from_indicators_with_macro"):
        closes = [item.close for item in candles]
        highs = [item.high for item in candles]
        lows = [item.low for item in candles]
        fast_series = ema(closes, strategy.fast_period)
        slow_series = ema(closes, strategy.slow_period)
        macro_series = ema(closes, strategy.macro_period)
        rsi_series = compute_rsi(closes, strategy.rsi_period)
        atr_values = atr_series(highs, lows, closes, strategy.atr_period)

        def signal_at(index: int) -> tuple:
            fast_value = fast_series[index - 1]
            slow_value = slow_series[index - 1]
            close_value = closes[index - 1]
            gap = abs(fast_value - slow_value) / close_value if close_value else 0.0
            return (
                strategy.signal_from_indicators_with_macro(
                    fast_value, slow_value, macro_series[index - 1],
                    rsi_series[index - 1], atr_values[index - 1], close_value,
                ),
                rsi_series[index - 1],
                gap,
            )
    elif hasattr(strategy, "signal_from_indicators"):
        closes = [item.close for item in candles]
        highs = [item.high for item in candles]
        lows = [item.low for item in candles]
        fast_series = ema(closes, strategy.fast_period)
        slow_series = ema(closes, strategy.slow_period)
        rsi_series = compute_rsi(closes, strategy.rsi_period)
        atr_values = atr_series(highs, lows, closes, strategy.atr_period)

        def signal_at(index: int) -> tuple:
            fast_value = fast_series[index - 1]
            slow_value = slow_series[index - 1]
            close_value = closes[index - 1]
            gap = abs(fast_value - slow_value) / close_value if close_value else 0.0
            return (
                strategy.signal_from_indicators(
                    fast_value, slow_value,
                    rsi_series[index - 1], atr_values[index - 1],
                ),
                rsi_series[index - 1],
                gap,
            )
    else:
        def signal_at(index: int) -> tuple:
            window = candles[:index]
            closes = [item.close for item in window]
            return strategy.evaluate(window), compute_rsi(closes, strategy.rsi_period)[-1], None

    for index in range(strategy.minimum_candles, len(candles)):
        signal, rsi_value, ema_gap = signal_at(index)
        if signal is None:
            continue
        label = signal.direction.value.upper()
        if label == last_signal:
            continue
        last_signal = label
        decision_candle = candles[index - 1]
        observations.append(
            SyntheticObservation(
                observed_at=decision_candle.started_at,
                spot=decision_candle.close,
                signal=label,
                rsi=rsi_value,
                atr=signal.stop_distance,
                confidence=signal.confidence,
                ema_gap_normalized=ema_gap,
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
    include_derived: bool = False,
) -> BacktestResult:
    """Replay Upstox-sourced underlying and option candles for backtesting.

    Only reads ``market_candles``/``instruments`` rows tagged ``source='upstox'``
    — Angel-sourced data in the same archive is never touched or mixed in.
    By default also excludes any candle tagged ``derived_from_timeframe``
    (resampled from a finer timeframe rather than fetched directly from
    Upstox) so a research script materializing derived candles elsewhere in
    the archive can never silently change this engine's results — see
    ``save_upstox_candles``'s docstring and ``BACKTEST_FINDINGS.md``'s
    2026-08-21 data-integrity entry, where exactly that happened.

    Pass ``include_derived=True`` to knowingly include derived candles too
    — e.g. to backtest a period real Upstox data was never pulled for, using
    only candles resampled from already-archived, real, finer-grained data.
    Only opt into this for a range/analysis you're explicitly labeling as
    using derived data; never as the silent default.
    """
    strategy = strategy or MomentumStrategy()
    variant = parameters or BacktestParameters()
    derived_filter = "" if include_derived else " AND derived_from_timeframe IS NULL"
    with archive.connect() as con:
        clauses = ["instrument_token=?", "source='upstox'", "timeframe=?"]
        if not include_derived:
            clauses.append("derived_from_timeframe IS NULL")
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

        # (opening_range_pct, close_time_of_the_range) -- a signal observed
        # before the range has actually closed can't use this value without
        # lookahead, so callers must also check observed_at against the
        # stored close time (see the filter below).
        opening_range_by_day: dict[object, tuple[float, datetime]] = {}
        if variant.minimum_opening_range_pct is not None:
            candles_by_day: dict[object, list[Candle]] = {}
            for candle in underlying_candles:
                candles_by_day.setdefault(candle.started_at.date(), []).append(candle)
            for day, day_candles in candles_by_day.items():
                day_candles.sort(key=lambda c: c.started_at)
                opening = day_candles[: variant.opening_range_bars]
                if len(opening) < variant.opening_range_bars:
                    continue
                range_low = min(c.low for c in opening)
                range_high = max(c.high for c in opening)
                if range_low > 0:
                    opening_range_by_day[day] = ((range_high - range_low) / range_low, opening[-1].started_at)

        # The set of instrument tokens with usable Upstox candles is constant
        # for the whole run -- computing it once into a temp table (instead of
        # a fresh full-table DISTINCT scan inside the per-observation contract
        # query below) avoids re-scanning all of market_candles once per
        # signal, which becomes minutes-per-call once the archive is large
        # (e.g. the 2026-08-21 archive-extension work: a 15-month backtest
        # against a multi-million-row archive went from effectively hanging
        # to seconds after this fix).
        con.execute("DROP TABLE IF EXISTS temp.available_upstox_tokens")
        con.execute(
            f"""CREATE TEMP TABLE available_upstox_tokens AS
                SELECT DISTINCT instrument_token FROM market_candles
                WHERE source='upstox'{derived_filter}"""
        )
        con.execute(
            "CREATE INDEX temp.available_upstox_tokens_idx ON available_upstox_tokens(instrument_token)"
        )

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
            if (
                variant.minimum_signal_confidence
                and observation.confidence < variant.minimum_signal_confidence
            ):
                continue
            if variant.minimum_ema_separation and (
                observation.ema_gap_normalized is None
                or observation.ema_gap_normalized < variant.minimum_ema_separation
            ):
                continue
            if variant.minimum_opening_range_pct is not None:
                day_range = opening_range_by_day.get(observed_at.date())
                if (
                    day_range is None
                    or observed_at <= day_range[1]  # signal fires before the range even closed -- can't use it
                    or day_range[0] < variant.minimum_opening_range_pct
                ):
                    continue
            option_type = "CE" if observation.signal == "BULLISH" else "PE"
            contract = con.execute(
                """SELECT i.token, i.lot_size, i.symbol, i.expiry
                   FROM instruments i
                   WHERE i.underlying='NIFTY' AND i.option_type=? AND i.expiry>=date(?)
                     AND i.token IN (SELECT instrument_token FROM available_upstox_tokens)
                   ORDER BY i.expiry, ABS(i.strike-?) LIMIT 1""",
                (option_type, observed_at.isoformat(), observation.spot),
            ).fetchone()
            if contract is None:
                continue
            if variant.exclude_expiry_day and contract[3] == observed_at.date().isoformat():
                continue
            entry = con.execute(
                f"""SELECT started_at, open FROM market_candles
                   WHERE instrument_token=? AND source='upstox'{derived_filter}
                     AND started_at>? AND date(started_at)=?
                   ORDER BY started_at LIMIT 1""",
                (contract[0], observed_at.isoformat(), observed_at.date().isoformat()),
            ).fetchone()
            if entry is None:
                continue
            if variant.minimum_option_premium and float(entry[1]) < variant.minimum_option_premium:
                continue
            if variant.minimum_open_interest:
                oi_row = con.execute(
                    f"""SELECT open_interest FROM market_candles
                       WHERE instrument_token=? AND source='upstox'{derived_filter}
                         AND started_at<=? ORDER BY started_at DESC LIMIT 1""",
                    (contract[0], observed_at.isoformat()),
                ).fetchone()
                open_interest = float(oi_row[0]) if oi_row is not None and oi_row[0] is not None else None
                if open_interest is None or open_interest < variant.minimum_open_interest:
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
                f"""SELECT started_at, open, high, low, close FROM market_candles
                   WHERE instrument_token=? AND source='upstox'{derived_filter}
                     AND started_at>=? AND started_at<=?
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
                trailing_active = variant.trailing_activation_return is None
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
                    if (
                        not trailing_active
                        and variant.trailing_activation_return
                        and peak_price >= buy_fill * (1 + variant.trailing_activation_return)
                    ):
                        trailing_active = True
                    if variant.trailing_stop and trailing_active:
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
    return build_backtest_result(
        trades, archive, settings, trading_days, source="upstox", start=start, end=end
    )
