"""Explainable, aggregate-only analysis of Upstox-backtested trades.

Every number here is a plain count, mean, or rate over already-computed
trade fields — no model, no fitting, nothing that "learns" from the data.
This mirrors the anti-overfitting discipline already used in
``validation.py``: a suggestion is a hypothesis to manually retest on
held-out data through the existing development/validation/test split, not a
conclusion to trust outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .backtest import BacktestParameters, BacktestResult, OptionBacktestTrade
from .candles import Candle
from .config import Settings
from .market_archive import MarketArchive
from .strategy import MomentumStrategy
from .upstox_backtest import generate_signals_from_candles, run_upstox_backtest
from .upstox_ingest import NIFTY_UNDERLYING_KEY
from .validation import STRATEGY_VARIANTS

DEFAULT_MINIMUM_SAMPLE = 20
MINIMUM_WIN_RATE_GAP_PP = 10.0
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class TradeBreakdown:
    label: str
    trades: int
    win_rate: float | None
    profit_factor: float | None
    average_net_pnl: float | None


@dataclass(frozen=True)
class VariantComparison:
    name: str
    result: BacktestResult


@dataclass(frozen=True)
class DeepAnalysisReport:
    overall: BacktestResult
    time_of_day: tuple[TradeBreakdown, ...]
    day_of_week: tuple[TradeBreakdown, ...]
    expiry_day: tuple[TradeBreakdown, ...]
    volatility_regime: tuple[TradeBreakdown, ...]
    variants: tuple[VariantComparison, ...]


@dataclass(frozen=True)
class Suggestion:
    """A plain, traceable hypothesis mined from historical data — not a proven edge."""

    headline: str
    evidence: str
    supporting_trades: int


def _summarize(label: str, trades: list[OptionBacktestTrade]) -> TradeBreakdown:
    if not trades:
        return TradeBreakdown(label, 0, None, None, None)
    net_values = [trade.net_pnl for trade in trades]
    winners = sum(value > 0 for value in net_values)
    gains = sum(value for value in net_values if value > 0)
    losses = abs(sum(value for value in net_values if value < 0))
    return TradeBreakdown(
        label=label,
        trades=len(trades),
        win_rate=winners / len(trades),
        profit_factor=(gains / losses) if losses else None,
        average_net_pnl=sum(net_values) / len(trades),
    )


def _hourly_bands(entry_start: time, entry_cutoff: time) -> list[tuple[time, time, str]]:
    anchor = date(2000, 1, 1)
    cursor = datetime.combine(anchor, entry_start)
    end = datetime.combine(anchor, entry_cutoff)
    bands: list[tuple[time, time, str]] = []
    while cursor < end:
        band_end = min(cursor + timedelta(hours=1), end)
        label = f"{cursor.strftime('%H:%M')}-{band_end.strftime('%H:%M')}"
        bands.append((cursor.time(), band_end.time(), label))
        cursor = band_end
    return bands


def _time_of_day_breakdown(
    trades: list[OptionBacktestTrade], entry_start: time, entry_cutoff: time
) -> tuple[TradeBreakdown, ...]:
    bands = _hourly_bands(entry_start, entry_cutoff)
    groups: dict[str, list[OptionBacktestTrade]] = {label: [] for _, _, label in bands}
    other: list[OptionBacktestTrade] = []
    for trade in trades:
        trade_time = trade.signal_at.time()
        for band_start, band_end, label in bands:
            if band_start <= trade_time < band_end:
                groups[label].append(trade)
                break
        else:
            other.append(trade)
    breakdowns = [_summarize(label, groups[label]) for _, _, label in bands]
    if other:
        breakdowns.append(_summarize("Outside entry window", other))
    return tuple(breakdowns)


def _day_of_week_breakdown(trades: list[OptionBacktestTrade]) -> tuple[TradeBreakdown, ...]:
    groups: dict[int, list[OptionBacktestTrade]] = {index: [] for index in range(7)}
    for trade in trades:
        groups[trade.signal_at.weekday()].append(trade)
    return tuple(
        _summarize(WEEKDAY_NAMES[day], group) for day, group in groups.items() if group
    )


def _expiry_day_breakdown(
    trades: list[OptionBacktestTrade], archive: MarketArchive
) -> tuple[TradeBreakdown, ...]:
    if not trades:
        return ()
    tokens = {trade.token for trade in trades}
    placeholders = ",".join("?" for _ in tokens)
    with archive.connect() as con:
        rows = con.execute(
            f"SELECT token, expiry FROM instruments WHERE token IN ({placeholders})",
            tuple(tokens),
        ).fetchall()
    expiry_by_token = {row[0]: row[1] for row in rows}
    expiry_day: list[OptionBacktestTrade] = []
    other: list[OptionBacktestTrade] = []
    for trade in trades:
        bucket = (
            expiry_day
            if expiry_by_token.get(trade.token) == trade.signal_at.date().isoformat()
            else other
        )
        bucket.append(trade)
    return (_summarize("Expiry day", expiry_day), _summarize("Non-expiry day", other))


def _atr_lookup(
    archive: MarketArchive,
    strategy: MomentumStrategy,
    start: date | None,
    end: date | None,
    underlying_key: str,
    timeframe: str,
) -> dict[str, float]:
    """Recompute the walk-forward signal ATR values, keyed by observation timestamp."""
    clauses = ["instrument_token=?", "source='upstox'", "timeframe=?"]
    sql_parameters: list[object] = [underlying_key, timeframe]
    if start:
        clauses.append("date(started_at)>=?")
        sql_parameters.append(start.isoformat())
    if end:
        clauses.append("date(started_at)<=?")
        sql_parameters.append(end.isoformat())
    where = " AND ".join(clauses)
    with archive.connect() as con:
        rows = con.execute(
            f"""SELECT started_at, symbol, open, high, low, close FROM market_candles
                WHERE {where} ORDER BY started_at""",
            sql_parameters,
        ).fetchall()
    candles = [
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
    observations = generate_signals_from_candles(candles, strategy)
    return {observation.observed_at.isoformat(): observation.atr for observation in observations}


def _volatility_regime_breakdown(
    trades: list[OptionBacktestTrade], atr_by_signal_at: dict[str, float]
) -> tuple[TradeBreakdown, ...]:
    known = [
        (trade, atr_by_signal_at[trade.signal_at.isoformat()])
        for trade in trades
        if trade.signal_at.isoformat() in atr_by_signal_at
    ]
    if len(known) < 3:
        return ()
    atr_values = sorted(atr for _, atr in known)
    low_cut = atr_values[len(atr_values) // 3]
    high_cut = atr_values[(2 * len(atr_values)) // 3]
    low_group = [trade for trade, atr in known if atr <= low_cut]
    mid_group = [trade for trade, atr in known if low_cut < atr < high_cut]
    high_group = [trade for trade, atr in known if atr >= high_cut]
    return (
        _summarize("Low volatility", low_group),
        _summarize("Medium volatility", mid_group),
        _summarize("High volatility", high_group),
    )


def run_deep_analysis(
    archive: MarketArchive,
    strategy: MomentumStrategy | None = None,
    start: date | None = None,
    end: date | None = None,
    settings: Settings | None = None,
    variants: tuple[BacktestParameters, ...] = STRATEGY_VARIANTS,
    underlying_key: str = NIFTY_UNDERLYING_KEY,
    timeframe: str = "FIVE_MINUTE",
) -> DeepAnalysisReport:
    """Run the baseline Upstox backtest plus explainable statistical breakdowns."""
    strategy = strategy or MomentumStrategy()
    baseline = variants[0] if variants else BacktestParameters()
    overall = run_upstox_backtest(
        archive, strategy, start, end, settings, baseline, underlying_key, timeframe
    )
    trades = list(overall.trade_details)
    atr_by_signal_at = _atr_lookup(archive, strategy, start, end, underlying_key, timeframe)
    entry_start = settings.entry_start if settings else time(9, 15)
    entry_cutoff = settings.entry_cutoff if settings else time(15, 20)

    return DeepAnalysisReport(
        overall=overall,
        time_of_day=_time_of_day_breakdown(trades, entry_start, entry_cutoff),
        day_of_week=_day_of_week_breakdown(trades),
        expiry_day=_expiry_day_breakdown(trades, archive),
        volatility_regime=_volatility_regime_breakdown(trades, atr_by_signal_at),
        variants=tuple(
            VariantComparison(
                variant.name,
                run_upstox_backtest(
                    archive, strategy, start, end, settings, variant, underlying_key, timeframe
                ),
            )
            for variant in variants
        ),
    )


def _compare_breakdown(
    dimension: str, breakdown: tuple[TradeBreakdown, ...], minimum_sample: int
) -> list[Suggestion]:
    eligible = [
        item for item in breakdown if item.trades >= minimum_sample and item.win_rate is not None
    ]
    if len(eligible) < 2:
        return []
    best = max(eligible, key=lambda item: item.win_rate)
    worst = min(eligible, key=lambda item: item.win_rate)
    if best.label == worst.label:
        return []
    gap_pp = (best.win_rate - worst.win_rate) * 100
    if gap_pp < MINIMUM_WIN_RATE_GAP_PP:
        return []
    return [
        Suggestion(
            headline=(
                f"By {dimension}: '{best.label}' win rate is {gap_pp:.1f} percentage "
                f"points higher than '{worst.label}'."
            ),
            evidence=(
                f"'{best.label}': {best.win_rate * 100:.1f}% over {best.trades} trades. "
                f"'{worst.label}': {worst.win_rate * 100:.1f}% over {worst.trades} trades."
            ),
            supporting_trades=best.trades + worst.trades,
        )
    ]


def generate_suggestions(
    report: DeepAnalysisReport, minimum_sample: int = DEFAULT_MINIMUM_SAMPLE
) -> tuple[Suggestion, ...]:
    """Emit plain, traceable comparative statements — never a fitted model.

    A suggestion only appears when both compared buckets have at least
    ``minimum_sample`` trades, mirroring the sample-size discipline already
    used elsewhere in this project's validation workspace.
    """
    suggestions: list[Suggestion] = []
    suggestions.extend(_compare_breakdown("time of day", report.time_of_day, minimum_sample))
    suggestions.extend(_compare_breakdown("day of week", report.day_of_week, minimum_sample))
    suggestions.extend(
        _compare_breakdown("expiry-day status", report.expiry_day, minimum_sample)
    )
    suggestions.extend(
        _compare_breakdown("volatility regime", report.volatility_regime, minimum_sample)
    )
    return tuple(suggestions)
