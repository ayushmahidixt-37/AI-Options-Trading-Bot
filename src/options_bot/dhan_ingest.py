"""Backfill pre-Oct-2024 NIFTY option data from DhanHQ into the archive.

Only reads from DhanHQ and writes read-only historical rows into
``MarketArchive`` tagged ``source="dhan"``. No order-placement capability.

Reconstruction approach (see BACKTEST_FINDINGS.md's 2026-08-23 DhanHQ entry
for the full investigation and the checks that validated it):

* Dhan's rolling-option endpoint only accepts *ATM-relative* strike labels,
  each of which whipsaws between adjacent absolute strikes minute-to-minute.
  We fetch a wide band (ATM-10..ATM+10) and re-group every returned point by
  its real absolute strike, which reconstructs a genuine fixed-strike,
  continuous-minute series -- exactly like a real single option contract.
* Expiries roll over roughly weekly, and Dhan's only working expiry code
  (``expiryCode=1``) resolves to "nearest expiry for the requested range" --
  so every request here is scoped to fall within one NIFTY weekly cycle
  (Fri..Thu) to avoid a request silently splicing two different contracts'
  data together.
* The weekly-cycle boundary is plain Thursday arithmetic, not adjusted for
  exchange holidays that occasionally shift a real expiry to Wednesday -- a
  boundary trading day can rarely be attributed to the wrong cycle. This is a
  deliberate, documented simplification, not an oversight.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from time import sleep
from typing import Callable

from .candle_resample import resample_candles
from .candles import Candle
from .dhan_data import DhanClient, DhanDataError, IST, nifty_lot_size
from .domain import Instrument
from .market_archive import MarketArchive
from .upstox_data import UpstoxCandle

RESAMPLE_TARGET_TIMEFRAME = "FIVE_MINUTE"

STRIKE_OFFSETS = tuple(range(-10, 11))  # ATM-10 .. ATM+10, per the user's explicit choice
OPTION_TYPES = (("CALL", "CE"), ("PUT", "PE"))
TIMEFRAME = "ONE_MINUTE"
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 8.0
DEFAULT_MAX_WORKERS = 6  # concurrent Dhan requests; each worker still retries+backs off on its own

# Matches upstox_backtest.py's NIFTY_UNDERLYING_KEY exactly -- storing Dhan's
# reconstructed spot candles under the SAME token (different `source`) is
# what lets a backtest read the underlying series across both sources
# without any special-casing.
NIFTY_UNDERLYING_TOKEN = "NSE_INDEX|Nifty 50"
INDEX_CHUNK_DAYS = 85  # a safety margin under Dhan's documented 90-day-per-call cap


@dataclass(frozen=True)
class FailedRequest:
    """One (strike-offset label, option side) that failed after all retries.

    Kept structured (not just a warning string) so a caller can retry exactly
    these requests -- e.g. ``retry_failed_requests`` -- instead of redoing an
    entire cycle just to recover a handful of strikes.
    """

    strike_label: str
    option_type: str  # "CALL" or "PUT"
    cycle_start: date
    cycle_end: date
    expiry: date
    error: str


@dataclass(frozen=True)
class WeeklyCycleSummary:
    cycle_start: date
    cycle_end: date
    expiry: date
    candles_saved: int
    instruments_saved: int
    warnings: tuple[str, ...]
    failed_requests: tuple[FailedRequest, ...] = ()


def weekly_expiry_cycles(start: date, end: date) -> list[tuple[date, date, date]]:
    """Return ``(cycle_start, cycle_end, expiry)`` for each weekly cycle overlapping [start, end].

    ``expiry`` is that cycle's Thursday; ``cycle_start`` is the day after the
    previous Thursday (clamped to ``start``); ``cycle_end`` is the expiry
    Thursday itself (clamped to ``end``).
    """
    if start > end:
        raise ValueError("start must not be after end")
    cursor = start
    while cursor.weekday() != 3:  # Monday=0 .. Thursday=3
        cursor += timedelta(days=1)
    previous_expiry = cursor - timedelta(days=7)
    cycles: list[tuple[date, date, date]] = []
    while True:
        cycle_start = max(start, previous_expiry + timedelta(days=1))
        if cycle_start > end:
            break
        cycle_end = min(cursor, end)
        cycles.append((cycle_start, cycle_end, cursor))
        previous_expiry = cursor
        cursor += timedelta(days=7)
    return cycles


def _fetch_with_retry(
    client: DhanClient,
    *,
    strike_label: str,
    option_type: str,
    from_date: date,
    to_date: date,
    sleeper: Callable[[float], None] = sleep,
):
    last_error: DhanDataError | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.fetch_rolling_option(
                strike_label=strike_label,
                option_type=option_type,
                expiry_flag="WEEK",
                from_date=from_date,
                to_date=to_date,
            )
        except DhanDataError as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                sleeper(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_error  # type: ignore[misc]


def pull_weekly_cycle(
    client: DhanClient,
    archive: MarketArchive,
    cycle_start: date,
    cycle_end: date,
    expiry: date,
    *,
    observed_at: datetime | None = None,
    sleeper: Callable[[float], None] = sleep,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> WeeklyCycleSummary:
    """Fetch and store one weekly cycle's ATM-10..ATM+10 CE/PE band from Dhan.

    The 42 (strike-offset, option-type) requests for a cycle are independent
    of each other, so they're fetched concurrently over the network (a
    thread pool) rather than one at a time -- each worker still retries and
    backs off on its own via ``_fetch_with_retry``. Results are merged into
    ``by_side`` only after every future completes, so the actual database
    writes stay single-threaded (SQLite tolerates concurrent readers far
    better than concurrent writers).
    """
    now = observed_at or datetime.now(IST)
    warnings: list[str] = []
    failed_requests: list[FailedRequest] = []
    # option_type -> strike -> list of raw points, later reconstructed and sorted
    by_side: dict[str, dict[float, list]] = {"CE": {}, "PE": {}}

    requests = [
        ("ATM" if offset == 0 else f"ATM{offset:+d}", drv_type, side_key)
        for offset in STRIKE_OFFSETS
        for drv_type, side_key in OPTION_TYPES
    ]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _fetch_with_retry, client, strike_label=label, option_type=drv_type,
                from_date=cycle_start, to_date=cycle_end, sleeper=sleeper,
            ): (label, drv_type, side_key)
            for label, drv_type, side_key in requests
        }
        for future in as_completed(futures):
            label, drv_type, side_key = futures[future]
            try:
                points = future.result()
            except DhanDataError as exc:
                warnings.append(f"{label}/{drv_type} {cycle_start}..{cycle_end}: {exc}")
                failed_requests.append(
                    FailedRequest(
                        strike_label=label, option_type=drv_type, cycle_start=cycle_start,
                        cycle_end=cycle_end, expiry=expiry, error=str(exc),
                    )
                )
                continue
            for point in points:
                by_side[side_key].setdefault(point.strike, []).append(point)

    instruments_saved = 0
    candles_saved = 0
    lot_size = nifty_lot_size(expiry)
    for side_key, strikes in by_side.items():
        for strike, points in strikes.items():
            points.sort(key=lambda p: p.started_at)
            token = f"DHAN|NIFTY|{expiry.isoformat()}|{strike:g}|{side_key}"
            symbol = f"NIFTY{expiry.isoformat()}{strike:g}{side_key}"
            instrument = Instrument(
                symbol=symbol, token=token, exchange="NFO", underlying="NIFTY",
                option_type=side_key, lot_size=lot_size, expiry=expiry, strike=strike,
            )
            instruments_saved += archive.save_instruments([instrument], now)
            candles = [
                UpstoxCandle(
                    symbol=symbol, started_at=point.started_at, open=point.open,
                    high=point.high if point.high is not None else point.open,
                    low=point.low if point.low is not None else point.open,
                    close=point.close, open_interest=point.open_interest,
                    volume=point.volume, implied_volatility=point.implied_volatility,
                )
                for point in points
            ]
            candles_saved += archive.save_dhan_candles(
                candles, token=token, exchange="NFO", timeframe=TIMEFRAME, collected_at=now,
            )

    archive.record_run(
        now,
        "success" if not warnings else "partial",
        candles_saved,
        instruments_saved,
        f"source=dhan cycle={cycle_start.isoformat()}..{cycle_end.isoformat()} expiry={expiry.isoformat()}",
    )
    return WeeklyCycleSummary(
        cycle_start=cycle_start, cycle_end=cycle_end, expiry=expiry,
        candles_saved=candles_saved, instruments_saved=instruments_saved,
        warnings=tuple(warnings), failed_requests=tuple(failed_requests),
    )


def retry_failed_requests(
    client: DhanClient,
    archive: MarketArchive,
    failed: list[FailedRequest],
    *,
    observed_at: datetime | None = None,
    sleeper: Callable[[float], None] = sleep,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> tuple[int, list[FailedRequest]]:
    """Re-attempt exactly the requests that failed after all retries, save any that now succeed.

    Returns ``(candles_saved, still_failing)``. Safe to call any time after a
    backfill run -- storage is idempotent, so re-saving already-present
    candles is a no-op.
    """
    now = observed_at or datetime.now(IST)
    still_failing: list[FailedRequest] = []
    candles_saved = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _fetch_with_retry, client, strike_label=item.strike_label, option_type=item.option_type,
                from_date=item.cycle_start, to_date=item.cycle_end, sleeper=sleeper,
            ): item
            for item in failed
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                points = future.result()
            except DhanDataError as exc:
                still_failing.append(
                    FailedRequest(
                        strike_label=item.strike_label, option_type=item.option_type,
                        cycle_start=item.cycle_start, cycle_end=item.cycle_end,
                        expiry=item.expiry, error=str(exc),
                    )
                )
                continue
            side_key = "CE" if item.option_type == "CALL" else "PE"
            lot_size = nifty_lot_size(item.expiry)
            by_strike: dict[float, list] = {}
            for point in points:
                by_strike.setdefault(point.strike, []).append(point)
            for strike, strike_points in by_strike.items():
                strike_points.sort(key=lambda p: p.started_at)
                token = f"DHAN|NIFTY|{item.expiry.isoformat()}|{strike:g}|{side_key}"
                symbol = f"NIFTY{item.expiry.isoformat()}{strike:g}{side_key}"
                instrument = Instrument(
                    symbol=symbol, token=token, exchange="NFO", underlying="NIFTY",
                    option_type=side_key, lot_size=lot_size, expiry=item.expiry, strike=strike,
                )
                archive.save_instruments([instrument], now)
                candles = [
                    UpstoxCandle(
                        symbol=symbol, started_at=p.started_at, open=p.open,
                        high=p.high if p.high is not None else p.open,
                        low=p.low if p.low is not None else p.open,
                        close=p.close, open_interest=p.open_interest,
                        volume=p.volume, implied_volatility=p.implied_volatility,
                    )
                    for p in strike_points
                ]
                candles_saved += archive.save_dhan_candles(
                    candles, token=token, exchange="NFO", timeframe=TIMEFRAME, collected_at=now,
                )
    return candles_saved, still_failing


def _fetch_index_with_retry(
    client: DhanClient,
    *,
    from_date: datetime,
    to_date: datetime,
    sleeper: Callable[[float], None] = sleep,
):
    last_error: DhanDataError | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.fetch_index_intraday(from_date=from_date, to_date=to_date)
        except DhanDataError as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                sleeper(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_error  # type: ignore[misc]


def pull_index_range(
    client: DhanClient,
    archive: MarketArchive,
    start: date,
    end: date,
    *,
    observed_at: datetime | None = None,
    sleeper: Callable[[float], None] = sleep,
) -> tuple[int, list[str]]:
    """Backfill the real NIFTY spot index (not options) for [start, end].

    Stored under ``NIFTY_UNDERLYING_TOKEN`` -- the same token
    ``upstox_backtest.py`` already reads for the underlying series -- so
    signal generation can read straight through both sources without any
    special-casing. Chunked to ``INDEX_CHUNK_DAYS`` to respect Dhan's
    documented 90-day-per-call limit for intraday data.
    """
    now = observed_at or datetime.now(IST)
    warnings: list[str] = []
    candles_saved = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=INDEX_CHUNK_DAYS - 1))
        from_dt = datetime.combine(cursor, time(9, 15), tzinfo=IST)
        to_dt = datetime.combine(chunk_end, time(15, 30), tzinfo=IST)
        try:
            points = _fetch_index_with_retry(client, from_date=from_dt, to_date=to_dt, sleeper=sleeper)
        except DhanDataError as exc:
            warnings.append(f"index {cursor.isoformat()}..{chunk_end.isoformat()}: {exc}")
            cursor = chunk_end + timedelta(days=1)
            continue
        candles = [
            UpstoxCandle(symbol="NIFTY", started_at=p.started_at, open=p.open, high=p.high, low=p.low, close=p.close)
            for p in points
        ]
        candles_saved += archive.save_dhan_candles(
            candles, token=NIFTY_UNDERLYING_TOKEN, exchange="NSE_INDEX", timeframe=TIMEFRAME, collected_at=now,
        )
        cursor = chunk_end + timedelta(days=1)
    archive.record_run(
        now, "success" if not warnings else "partial", candles_saved, 0,
        f"source=dhan index range={start.isoformat()}..{end.isoformat()}",
    )
    return candles_saved, warnings


def pull_range(
    client: DhanClient,
    archive: MarketArchive,
    start: date,
    end: date,
    *,
    on_cycle_done: Callable[[WeeklyCycleSummary], None] | None = None,
    sleeper: Callable[[float], None] = sleep,
) -> list[WeeklyCycleSummary]:
    """Backfill every weekly cycle overlapping [start, end], oldest first."""
    cycles = weekly_expiry_cycles(start, end)
    summaries: list[WeeklyCycleSummary] = []
    for cycle_start, cycle_end, expiry in cycles:
        summary = pull_weekly_cycle(client, archive, cycle_start, cycle_end, expiry, sleeper=sleeper)
        summaries.append(summary)
        if on_cycle_done:
            on_cycle_done(summary)
    return summaries


SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


def _within_session(started_at: datetime) -> bool:
    """True for a genuine 09:15..15:30 (exclusive), on-the-minute candle.

    Found 2026-08-24 resampling the Dhan backfill: ~14% of the underlying's
    "1-minute" rows fall outside this (mostly post-15:30-close bars Dhan
    occasionally keeps emitting on some days, plus rarer odd-second
    pre-session outliers) -- resample_candles raises outright on anything
    before session_open, and nothing in this project's actual trading logic
    (entries/exits are all bounded within 09:15..15:30) ever needed data
    outside this window anyway, so it's filtered here rather than repaired.
    """
    return started_at.second == 0 and SESSION_OPEN <= started_at.time() < SESSION_CLOSE


def _bucket_start(started_at: datetime, bucket_minutes: int, session_open: time = time(9, 15)) -> datetime:
    """Same bucketing rule as candle_resample.resample_candles, exposed here
    so open-interest can be independently aggregated per bucket (that module
    only carries OHLC -- see resample_dhan_options_to_five_minute)."""
    minutes_since_open = (
        (started_at.hour - session_open.hour) * 60 + (started_at.minute - session_open.minute)
    )
    bucket_index = minutes_since_open // bucket_minutes
    return started_at.replace(
        hour=session_open.hour, minute=session_open.minute, second=0, microsecond=0
    ) + timedelta(minutes=bucket_index * bucket_minutes)


def resample_dhan_underlying_to_five_minute(
    archive: MarketArchive, *, observed_at: datetime | None = None
) -> int:
    """Resample the Dhan-sourced NIFTY spot 1-minute series to 5-minute bars.

    Needed to confirm Candidate B (built and validated on 5-minute bars)
    against the 2020-08..2024-10 Dhan backfill, which was deliberately
    fetched at 1-minute only. Saved under the same NIFTY_UNDERLYING_TOKEN,
    tagged ``derived_from_timeframe='ONE_MINUTE'`` so it's never confused
    with a directly-fetched 5-minute bar.
    """
    now = observed_at or datetime.now(IST)
    with archive.connect() as con:
        rows = con.execute(
            """SELECT symbol, started_at, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source='dhan' AND timeframe='ONE_MINUTE'
               ORDER BY started_at""",
            (NIFTY_UNDERLYING_TOKEN,),
        ).fetchall()
    candles = [
        Candle(
            symbol=r[0], started_at=datetime.fromisoformat(r[1]),
            open=r[2], high=r[3], low=r[4], close=r[5],
        )
        for r in rows
        if _within_session(datetime.fromisoformat(r[1]))
    ]
    resampled = resample_candles(candles, bucket_minutes=5, source_bucket_minutes=1)
    upstox_candles = [
        UpstoxCandle(symbol=c.symbol, started_at=c.started_at, open=c.open, high=c.high, low=c.low, close=c.close)
        for c in resampled
    ]
    return archive.save_dhan_candles(
        upstox_candles, token=NIFTY_UNDERLYING_TOKEN, exchange="NSE_INDEX",
        timeframe=RESAMPLE_TARGET_TIMEFRAME, collected_at=now,
    )


def resample_dhan_options_to_five_minute(
    archive: MarketArchive,
    *,
    observed_at: datetime | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Resample every Dhan-sourced 1-minute option contract to 5-minute bars.

    Open interest is preserved as the LAST value seen within each 5-minute
    bucket -- it's a snapshot/level, not a flow, so the most recent reading
    is the correct one to carry forward. candle_resample.resample_candles/
    Candle don't carry OI at all (a deliberately narrow, widely-shared
    utility not worth widening for this one caller), and Candidate B's
    minimum_open_interest=100000 filter needs a real value here or every
    trade would be silently rejected as "no OI data" once resampled.

    Volume is preserved as the SUM of the five 1-minute values in each
    bucket, not the last -- it's a flow (contracts traded during the
    interval), so summing gives the real 5-minute volume while taking the
    last minute's value would silently discard the other four fifths of the
    interval's activity. Added 2026-08-28 alongside the fetch/retry-path fix
    for the same field -- that fix alone was incomplete, since the strategy
    reads FIVE_MINUTE bars and this resample is what produces them; every
    prior resample of this dataset produced 0% volume coverage despite the
    1-minute source rows carrying real values.

    Returns (contracts_processed, candles_saved).
    """
    now = observed_at or datetime.now(IST)
    with archive.connect() as con:
        tokens = [
            row[0] for row in con.execute(
                """SELECT DISTINCT instrument_token FROM market_candles
                   WHERE source='dhan' AND timeframe='ONE_MINUTE' AND instrument_token LIKE 'DHAN|NIFTY|%'"""
            )
        ]
    candles_saved = 0
    for index, token in enumerate(tokens):
        with archive.connect() as con:
            rows = con.execute(
                """SELECT symbol, started_at, open, high, low, close, open_interest, volume
                   FROM market_candles WHERE instrument_token=? AND source='dhan' AND timeframe='ONE_MINUTE'
                   ORDER BY started_at""",
                (token,),
            ).fetchall()
        if not rows:
            continue
        candles = [
            Candle(
                symbol=r[0], started_at=datetime.fromisoformat(r[1]),
                open=r[2], high=r[3], low=r[4], close=r[5],
            )
            for r in rows
            if _within_session(datetime.fromisoformat(r[1]))
        ]
        last_oi_by_bucket: dict[datetime, float] = {}
        volume_sum_by_bucket: dict[datetime, float] = {}
        for r in rows:
            ts = datetime.fromisoformat(r[1])
            if not _within_session(ts):
                continue
            bucket = _bucket_start(ts, 5)
            if r[6] is not None:
                last_oi_by_bucket[bucket] = float(r[6])
            if r[7] is not None:
                volume_sum_by_bucket[bucket] = volume_sum_by_bucket.get(bucket, 0.0) + float(r[7])
        resampled = resample_candles(candles, bucket_minutes=5, source_bucket_minutes=1)
        upstox_candles = [
            UpstoxCandle(
                symbol=c.symbol, started_at=c.started_at, open=c.open, high=c.high, low=c.low, close=c.close,
                open_interest=last_oi_by_bucket.get(c.started_at),
                volume=volume_sum_by_bucket.get(c.started_at),
            )
            for c in resampled
        ]
        candles_saved += archive.save_dhan_candles(
            upstox_candles, token=token, exchange="NFO",
            timeframe=RESAMPLE_TARGET_TIMEFRAME, collected_at=now,
        )
        if on_progress:
            on_progress(index + 1, len(tokens))
    return len(tokens), candles_saved


def backfill_iv_for_weekly_cycle(
    client: DhanClient,
    archive: MarketArchive,
    cycle_start: date,
    cycle_end: date,
    *,
    sleeper: Callable[[float], None] = sleep,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> tuple[int, list[str]]:
    """Re-fetch one weekly cycle's ATM-10..ATM+10 CE/PE band and backfill
    implied_volatility onto the already-archived ONE_MINUTE rows (added to
    DhanClient.fetch_rolling_option's requiredData after the original
    backfill had already run, so it was never captured the first time --
    see backfill_implied_volatility's docstring). Does not insert any new
    candles. Returns (rows_updated, warnings).
    """
    warnings: list[str] = []
    # option_type -> strike -> list of (started_at, iv)
    by_side: dict[str, dict[float, list[tuple[datetime, float]]]] = {"CE": {}, "PE": {}}

    requests = [
        ("ATM" if offset == 0 else f"ATM{offset:+d}", drv_type, side_key)
        for offset in STRIKE_OFFSETS
        for drv_type, side_key in OPTION_TYPES
    ]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _fetch_with_retry, client, strike_label=label, option_type=drv_type,
                from_date=cycle_start, to_date=cycle_end, sleeper=sleeper,
            ): (label, drv_type, side_key)
            for label, drv_type, side_key in requests
        }
        for future in as_completed(futures):
            label, drv_type, side_key = futures[future]
            try:
                points = future.result()
            except DhanDataError as exc:
                warnings.append(f"{label}/{drv_type} {cycle_start}..{cycle_end}: {exc}")
                continue
            for point in points:
                if point.implied_volatility is not None:
                    by_side[side_key].setdefault(point.strike, []).append(
                        (point.started_at, point.implied_volatility)
                    )

    updated = 0
    expiry = weekly_expiry_cycles(cycle_start, cycle_end)
    # cycle_start..cycle_end is itself exactly one cycle, so this list has one element.
    resolved_expiry = expiry[0][2] if expiry else cycle_end
    for side_key, strikes in by_side.items():
        for strike, points in strikes.items():
            token = f"DHAN|NIFTY|{resolved_expiry.isoformat()}|{strike:g}|{side_key}"
            updated += archive.backfill_implied_volatility(token, TIMEFRAME, points)
    return updated, warnings


def resample_dhan_iv_to_five_minute(archive: MarketArchive) -> int:
    """Propagate implied_volatility (last value per bucket) from the
    ONE_MINUTE rows onto the already-resampled FIVE_MINUTE rows -- mirrors
    how open interest is carried through in resample_dhan_options_to_five_minute,
    done as a separate pass here since IV is backfilled after resampling
    already happened once.
    """
    with archive.connect() as con:
        tokens = [
            row[0] for row in con.execute(
                """SELECT DISTINCT instrument_token FROM market_candles
                   WHERE source='dhan' AND timeframe='FIVE_MINUTE' AND instrument_token LIKE 'DHAN|NIFTY|%'"""
            )
        ]
    updated = 0
    for token in tokens:
        with archive.connect() as con:
            one_min_rows = con.execute(
                """SELECT started_at, implied_volatility FROM market_candles
                   WHERE instrument_token=? AND source='dhan' AND timeframe='ONE_MINUTE'
                     AND implied_volatility IS NOT NULL
                   ORDER BY started_at""",
                (token,),
            ).fetchall()
        if not one_min_rows:
            continue
        last_iv_by_bucket: dict[datetime, float] = {}
        for started_at_str, iv in one_min_rows:
            ts = datetime.fromisoformat(started_at_str)
            if _within_session(ts):
                last_iv_by_bucket[_bucket_start(ts, 5)] = float(iv)
        if not last_iv_by_bucket:
            continue
        updates = list(last_iv_by_bucket.items())
        updated += archive.backfill_implied_volatility(token, RESAMPLE_TARGET_TIMEFRAME, updates)
    return updated
