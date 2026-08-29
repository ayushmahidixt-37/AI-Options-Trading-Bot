"""Offline replay of Upstox-sourced candles, filtered by a v2 ML model that
can use post-contract features (open interest, days-to-expiry) in addition
to the precontract features v1 (``upstox_ml_backtest.py``) supports.

Deliberately a separate module from ``upstox_ml_backtest.py`` -- mirroring
this project's own precedent (``upstox_backtest.py`` vs. ``backtest.py``,
``upstox_ml_backtest.py`` vs. ``upstox_backtest.py``) -- so the already-
tested v1 engine is never touched by this change.

v1's contract selection happens *during* trade construction, after the ML
decision. Post-contract features need the contract *before* the ML
decision, so this module selects each candidate signal's contract first,
computes every feature (precontract + postcontract) from it, then applies
the ML filter, then builds trades exactly as v1 does. Crucially, this must
preserve v1's exact sequencing rule: an observation that fails the ML
filter is removed from the surviving sequence (so it can never act as
another trade's exit-boundary trigger -- see ``upstox_ml_backtest.py``'s
docstring for why that matters), but an observation that passes the ML
filter yet turns out to have no valid contract (or is expiry-day-excluded)
stays *in* the surviving sequence -- it just produces no trade of its own,
identical to how v1 handles the same two cases. Getting this ordering
wrong would silently change every other trade's hold duration/exit price.
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
from .candles import Candle
from .config import Settings
from .market_archive import MarketArchive
from .ml_model import SignalQualityModel
from .strategy import MomentumStrategy
from .upstox_backtest import generate_signals_from_candles
from .upstox_ingest import NIFTY_UNDERLYING_KEY


def run_upstox_ml_backtest_v2(
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

    Unlike v1, ``model.feature_names`` may include
    ``ml_features.POSTCONTRACT_FEATURE_NAMES`` -- the contract each signal
    would use is selected before the ML decision so those features are
    available. A precontract-only model still works here (postcontract
    features are simply unused), so this function is a strict superset of
    v1's capability; v1 remains the one to use for a precontract-only model
    since it's simpler and already extensively tested.
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
        candidates = [
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

        # Select each candidate's contract (if any) and score it -- before
        # the ML filter runs, so postcontract features are available.
        scored: list[tuple] = []
        for observation in candidates:
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

            open_interest = None
            expiry = None
            if contract is not None:
                expiry = date.fromisoformat(contract[3])
                oi_row = con.execute(
                    f"""SELECT open_interest FROM market_candles
                       WHERE instrument_token=? AND source='upstox'{derived_filter}
                         AND started_at<=? ORDER BY started_at DESC LIMIT 1""",
                    (contract[0], observed_at.isoformat()),
                ).fetchone()
                if oi_row is not None and oi_row[0] is not None:
                    open_interest = float(oi_row[0])

            features = ml_features.extract_features_precontract(underlying_candles, observation, strategy)
            if any(name in model.feature_names for name in ml_features.POSTCONTRACT_FEATURE_NAMES):
                postcontract_expiry = expiry if expiry is not None else observed_at.date()
                features.update(
                    ml_features.extract_features_postcontract(observation, postcontract_expiry, open_interest)
                )
                if expiry is None:
                    # No contract at all -- days_to_expiry above is meaningless
                    # filler; open_interest_known already correctly says so.
                    features["days_to_expiry"] = 0.0
            scored.append((observation, contract, features))

        # ML filter -- an observation that fails is removed from the
        # sequence entirely, exactly like v1, so it can never define another
        # trade's exit boundary.
        survivors = [(observation, contract) for observation, contract, features in scored if model.decide(features)]

        trades: list[OptionBacktestTrade] = []
        for index, (observation, contract) in enumerate(survivors):
            observed_at = observation.observed_at
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
                survivors[index + 1][0].observed_at.isoformat()
                if index + 1 < len(survivors)
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
