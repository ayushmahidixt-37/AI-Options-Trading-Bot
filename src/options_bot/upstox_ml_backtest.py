"""Offline replay of Upstox-sourced candles, filtered by a trained ML model.

Deliberately separate from ``upstox_backtest.py`` -- mirroring that module's
own precedent relative to ``backtest.py`` -- so the already-tested engine is
never touched. It is *not* safe to run ``run_upstox_backtest`` unfiltered and
then discard ML-rejected trades afterward: ``run_upstox_backtest`` computes
each trade's signal-reversal exit boundary from the *next surviving
observation*, so a rejected signal sitting between two accepted ones would
otherwise still act as a premature exit trigger for the trade before it,
giving a kept trade the wrong hold duration/exit price/exit reason. This
module applies the ML decision to the observation list *before* trade
construction runs, at the same stage ``_observation_allowed`` already does,
so that trap cannot occur.

v1 models are trained on ``ml_features.FEATURE_NAMES`` (precontract-only). A
model whose ``feature_names`` includes any ``POSTCONTRACT_FEATURE_NAMES`` is
rejected here with a clear error -- that would require moving contract
selection ahead of the filter check and is explicitly deferred to a v2 model.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from . import ml_features
from .backtest import (
    BacktestParameters,
    BacktestResult,
    OptionBacktestTrade,
    _observation_allowed,
    build_backtest_result,
)
from .config import Settings
from .ml_model import SignalQualityModel
from .market_archive import MarketArchive
from .strategy import MomentumStrategy
from .upstox_backtest import generate_signals_from_candles
from .upstox_ingest import NIFTY_UNDERLYING_KEY
from .candles import Candle


def run_upstox_ml_backtest(
    archive: MarketArchive,
    model: SignalQualityModel,
    strategy: MomentumStrategy | None = None,
    start: date | None = None,
    end: date | None = None,
    settings: Settings | None = None,
    parameters: BacktestParameters | None = None,
    underlying_key: str = NIFTY_UNDERLYING_KEY,
    timeframe: str = "FIVE_MINUTE",
    include_derived: bool = False,
) -> BacktestResult:
    """Replay Upstox-sourced candles, keeping only ML-approved signals.

    ``parameters`` still controls the exit shell (stop/target/trailing/max-hold)
    and any non-ML filters (``entry_start``/``entry_end``/``allowed_weekdays``/
    ``exclude_expiry_day``/``minimum_atr``/``bullish_rsi_min``/``bearish_rsi_max``)
    -- those are applied first, exactly as in ``run_upstox_backtest``, and the
    ML filter is applied on top of whatever survives.

    ``include_derived`` mirrors ``run_upstox_backtest``'s parameter of the
    same name -- see that function's docstring. Off by default.
    """
    if any(name in model.feature_names for name in ml_features.POSTCONTRACT_FEATURE_NAMES):
        raise ValueError(
            "this backtest path only supports precontract-only models (v1); "
            f"model requests post-contract features {model.feature_names!r}"
        )

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

        # See run_upstox_backtest's identical comment: precompute the constant
        # available-token set once instead of re-scanning all of
        # market_candles per signal (catastrophic once the archive is large).
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
        # ML filter applied before trade construction -- see module docstring.
        observations = [
            observation
            for observation in observations
            if model.decide(
                ml_features.extract_features_precontract(underlying_candles, observation, strategy)
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
            if variant.stop_loss_pct is not None:
                # Percentage stop replaces the fixed-rupee one -- see
                # BacktestParameters.stop_loss_pct for why.
                has_stop_cap = True
                stop = round(buy_fill * (1 - variant.stop_loss_pct), 2)
            else:
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
    return build_backtest_result(
        trades, archive, settings, trading_days, source="upstox", start=start, end=end
    )
