"""Offline, read-only replay using only the local market archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from .candles import Candle
from .config import Settings
from .market_archive import MarketArchive
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


def run_momentum_backtest(
    archive: MarketArchive,
    start: date | None = None,
    end: date | None = None,
    settings: Settings | None = None,
) -> BacktestResult:
    """Replay archived signals and option candles without network or order calls."""
    clauses: list[str] = []
    parameters: list[str] = []
    if start:
        clauses.append("date(observed_at)>=?")
        parameters.append(start.isoformat())
    if end:
        clauses.append("date(observed_at)<=?")
        parameters.append(end.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with archive.connect() as con:
        observations = con.execute(
            f"""SELECT observed_at, spot, signal FROM strategy_observations
                {where} AND signal IN ('BULLISH','BEARISH')
                ORDER BY observed_at"""
            if where
            else """SELECT observed_at, spot, signal FROM strategy_observations
                     WHERE signal IN ('BULLISH','BEARISH') ORDER BY observed_at""",
            parameters,
        ).fetchall()
        observations = [
            row
            for index, row in enumerate(observations)
            if index == 0 or row[2] != observations[index - 1][2]
        ]
        trades: list[tuple[float, float]] = []
        for index, observation in enumerate(observations):
            observed_at = datetime.fromisoformat(observation[0])
            option_type = "CE" if observation[2] == "BULLISH" else "PE"
            contract = con.execute(
                """SELECT token, lot_size FROM instruments
                   WHERE underlying='NIFTY' AND option_type=? AND expiry>=date(?)
                   ORDER BY expiry, ABS(strike-?) LIMIT 1""",
                (option_type, observed_at.isoformat(), observation[1]),
            ).fetchone()
            if contract is None:
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
            next_observed = min(
                observations[index + 1][0]
                if index + 1 < len(observations)
                else session_exit,
                session_exit,
            )
            exit_row = con.execute(
                """SELECT close FROM market_candles
                   WHERE instrument_token=? AND started_at>? AND started_at<=?
                   ORDER BY started_at DESC LIMIT 1""",
                (contract[0], entry[0], next_observed),
            ).fetchone()
            if exit_row is not None:
                raw_points = float(exit_row[0]) - float(entry[1])
                slippage = settings.paper_slippage_bps / 10_000 if settings else 0.0
                buy_fill = round(float(entry[1]) * (1 + slippage), 2)
                sell_fill = round(float(exit_row[0]) * (1 - slippage), 2)
                units = int(contract[1])
                fees = 2 * settings.paper_fee_per_order if settings else 0.0
                net = round((sell_fill - buy_fill) * units - fees, 2)
                trades.append((raw_points, net))

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
    winners = sum(net > 0 for _, net in trades)
    net_values = [net for _, net in trades]
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
    return BacktestResult(
        "READY",
        len(trades),
        winners,
        losers,
        sum(points for points, _ in trades),
        winners / len(trades),
        sum(net_values),
        fees_paid,
        max_drawdown,
        profit_factor,
        "Entry uses the next archived option candle open; results apply configured lot size, fees, and slippage.",
    )
