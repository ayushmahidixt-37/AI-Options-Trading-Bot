"""Offline, read-only replay using only the local market archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import csv
from pathlib import Path

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
    trade_details: tuple["OptionBacktestTrade", ...] = ()
    trading_days: int = 0
    data_gaps: int = 0


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
        trading_days = int(
            con.execute(
                "SELECT COUNT(DISTINCT date(observed_at)) FROM strategy_observations"
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
                """SELECT token, lot_size, symbol FROM instruments
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
            path = con.execute(
                """SELECT started_at, open, low, close FROM market_candles
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
            risk_budget = settings.max_loss_per_trade * 0.8 if settings else float("inf")
            stop_distance = (risk_budget - fees) / units if settings else float("inf")
            stop = round(buy_fill - stop_distance, 2) if settings else 0.0
            selected_exit = path[-1]
            exit_price = float(selected_exit[3])
            exit_reason = (
                "signal-reversal"
                if index + 1 < len(observations) and next_observed != session_exit
                else "force-exit"
            )
            if settings and stop > 0:
                for candle in path:
                    if float(candle[1]) <= stop:
                        selected_exit, exit_price, exit_reason = candle, float(candle[1]), "stop-gap"
                        break
                    if float(candle[2]) <= stop:
                        selected_exit, exit_price, exit_reason = candle, stop, "stop"
                        break
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
    gaps = sum(int(item["gaps"]) for item in archive.gap_summary())
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
