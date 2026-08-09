"""Discover and pull Upstox historical NIFTY option data into the archive.

This module only reads from Upstox and writes read-only historical rows into
``MarketArchive`` (tagged ``source="upstox"``). It has no order-placement
capability and must never be used for anything beyond backtesting data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from time import monotonic, sleep
from typing import Callable

from .domain import Instrument
from .market_archive import MarketArchive
from .upstox_data import UpstoxClient, UpstoxDataError, parse_candle_row

NIFTY_UNDERLYING_KEY = "NSE_INDEX|Nifty 50"
DEFAULT_STRIKE_BAND = 5
DEFAULT_CHUNK_DAYS = 28
DEFAULT_MIN_CALL_INTERVAL_SECONDS = 0.05


@dataclass(frozen=True)
class ContractPlan:
    instrument: Instrument
    expired_instrument_key: str
    expiry: date
    pull_start: date
    pull_end: date


@dataclass(frozen=True)
class IngestionSummary:
    contracts_planned: int
    contracts_pulled: int
    candles_saved: int
    instruments_saved: int
    warnings: tuple[str, ...]


def chunk_date_range(
    start: date, end: date, chunk_days: int = DEFAULT_CHUNK_DAYS
) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into non-overlapping spans no longer than ``chunk_days``.

    Upstox's own documentation advises chunking finer-interval requests
    rather than assuming an unlimited per-call date range.
    """
    if start > end:
        raise ValueError("start must not be after end")
    if chunk_days < 1:
        raise ValueError("chunk_days must be positive")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


class RateLimiter:
    """Minimum-interval pacer kept safely under Upstox's documented limits."""

    def __init__(
        self,
        min_interval_seconds: float = DEFAULT_MIN_CALL_INTERVAL_SECONDS,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._last_call: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last_call is not None:
            remaining = self._min_interval - (now - self._last_call)
            if remaining > 0:
                self._sleeper(remaining)
        self._last_call = self._clock()


def _reference_spot(strikes: list[float]) -> float:
    """Approximate ATM using the median strike when no live spot price is known."""
    if not strikes:
        raise UpstoxDataError("No strikes available to approximate ATM")
    return strikes[len(strikes) // 2]


def plan_ingestion(
    client: UpstoxClient,
    start: date,
    end: date,
    *,
    max_lookback_days: int,
    strike_band: int = DEFAULT_STRIKE_BAND,
    underlying_key: str = NIFTY_UNDERLYING_KEY,
    observed_today: date | None = None,
) -> tuple[list[ContractPlan], tuple[str, ...]]:
    """Discover expired NIFTY option contracts covering ``[start, end]``.

    Returns the flat contract plan plus any warnings — most notably when part
    of the requested range falls outside Upstox's ~6-month expiry-discovery
    ceiling, which is trimmed rather than left to fail mid-request.
    """
    today = observed_today or date.today()
    oldest_allowed = today - timedelta(days=max_lookback_days)
    warnings: tuple[str, ...] = ()
    if start < oldest_allowed:
        warnings = (
            f"Requested start {start.isoformat()} is older than Upstox's "
            f"discoverable expiry window (~{max_lookback_days} days); data "
            f"before {oldest_allowed.isoformat()} will not be available.",
        )
        start = oldest_allowed

    all_expiries = client.get_expiries(underlying_key)
    relevant_expiries = sorted(
        expiry for expiry in all_expiries if start <= expiry <= end + timedelta(days=1)
    )

    plans: list[ContractPlan] = []
    for expiry in relevant_expiries:
        contracts = client.get_expired_option_contracts(underlying_key, expiry)
        if not contracts:
            continue
        strikes = sorted({float(item["strike_price"]) for item in contracts})
        reference_spot = _reference_spot(strikes)
        atm_index = min(
            range(len(strikes)), key=lambda index: abs(strikes[index] - reference_spot)
        )
        selected_strikes = set(
            strikes[max(0, atm_index - strike_band) : atm_index + strike_band + 1]
        )
        contract_start = max(start, expiry - timedelta(days=7))
        contract_end = min(end, expiry)
        if contract_start > contract_end:
            continue
        for item in contracts:
            strike = float(item["strike_price"])
            if strike not in selected_strikes:
                continue
            token = str(item["expired_instrument_key"])
            instrument = Instrument(
                symbol=str(item.get("trading_symbol", token)),
                token=token,
                exchange="NFO",
                underlying="NIFTY",
                option_type=str(item["instrument_type"]),
                lot_size=int(item.get("lot_size", 1)),
                expiry=expiry,
                strike=strike,
            )
            plans.append(
                ContractPlan(
                    instrument=instrument,
                    expired_instrument_key=token,
                    expiry=expiry,
                    pull_start=contract_start,
                    pull_end=contract_end,
                )
            )
    return plans, warnings


def pull_range(
    client: UpstoxClient,
    archive: MarketArchive,
    start: date,
    end: date,
    *,
    max_lookback_days: int,
    interval: str = "5minute",
    timeframe: str = "FIVE_MINUTE",
    strike_band: int = DEFAULT_STRIKE_BAND,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    underlying_key: str = NIFTY_UNDERLYING_KEY,
    rate_limiter: RateLimiter | None = None,
    observed_at: datetime | None = None,
) -> IngestionSummary:
    """Pull expired-option and underlying candles for ``[start, end]`` into the archive.

    Every write uses ``source="upstox"`` and reuses the archive's existing
    duplicate-safe ``INSERT OR IGNORE`` behavior, so re-running over an
    overlapping range is always safe.
    """
    limiter = rate_limiter or RateLimiter()
    now = observed_at or datetime.now()
    plans, warnings = plan_ingestion(
        client, start, end, max_lookback_days=max_lookback_days, strike_band=strike_band,
        underlying_key=underlying_key, observed_today=now.date(),
    )
    run_warnings = list(warnings)
    candles_saved = 0
    instruments_saved = 0
    contracts_pulled = 0

    for plan in plans:
        instruments_saved += archive.save_instruments([plan.instrument], now)
        for chunk_start, chunk_end in chunk_date_range(plan.pull_start, plan.pull_end, chunk_days):
            limiter.wait()
            try:
                raw_candles = client.get_expired_historical_candles(
                    plan.expired_instrument_key, chunk_start, chunk_end, interval=interval
                )
            except UpstoxDataError as exc:
                run_warnings.append(
                    f"{plan.expired_instrument_key} {chunk_start}-{chunk_end}: {exc}"
                )
                continue
            parsed = [parse_candle_row(plan.instrument.symbol, row) for row in raw_candles]
            candles_saved += archive.save_upstox_candles(
                parsed,
                token=plan.expired_instrument_key,
                exchange=plan.instrument.exchange,
                timeframe=timeframe,
                collected_at=now,
            )
        contracts_pulled += 1

    for chunk_start, chunk_end in chunk_date_range(start, end, chunk_days):
        limiter.wait()
        try:
            raw_underlying = client.get_historical_candles_v3(
                underlying_key, "minutes", "5", chunk_start, chunk_end
            )
        except UpstoxDataError as exc:
            run_warnings.append(f"underlying {chunk_start}-{chunk_end}: {exc}")
            continue
        parsed_underlying = [parse_candle_row("NIFTY", row) for row in raw_underlying]
        candles_saved += archive.save_upstox_candles(
            parsed_underlying,
            token=underlying_key,
            exchange="NSE_INDEX",
            timeframe=timeframe,
            collected_at=now,
        )

    archive.record_run(
        now,
        "success" if not run_warnings else "partial",
        candles_saved,
        instruments_saved,
        f"source=upstox range={start.isoformat()}..{end.isoformat()} contracts={contracts_pulled}",
    )
    return IngestionSummary(
        contracts_planned=len(plans),
        contracts_pulled=contracts_pulled,
        candles_saved=candles_saved,
        instruments_saved=instruments_saved,
        warnings=tuple(run_warnings),
    )
