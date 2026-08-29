"""Aggregate candles of any source granularity into a coarser timeframe.

Pure and stateless -- no archive access. Buckets are computed relative to
the session open per calendar day (default 09:15 IST), not clock-hour
boundaries, so this works correctly for bucket sizes that don't evenly
divide an hour (e.g. NIFTY's 375-minute session isn't a clean multiple of
10 minutes). A bucket is only emitted once it has exactly
``bucket_minutes // source_bucket_minutes`` underlying candles -- an
incomplete trailing bucket (a data gap, or the session's tail end for a
bucket size that doesn't evenly divide the session length) is dropped by
default rather than silently emitted with a misleadingly "complete-looking"
OHLC built from fewer candles than every other bucket. This mirrors this
project's existing fail-closed convention (``indicators.py``'s RSI/ATR
return ``None`` rather than a value computed from an incomplete warm-up
window). ``source_bucket_minutes`` defaults to 1 (the original 1-minute-only
behaviour); pass e.g. ``source_bucket_minutes=5`` to build 15-minute bars
out of 5-minute candles -- the original single-purpose version of this
function silently assumed 1-minute input and produced wrong bucket-
completeness checks (and therefore silently dropped everything) if fed
anything coarser; see BACKTEST_FINDINGS.md's multi-timeframe entries for
why that mattered.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import time

from .candles import Candle

DEFAULT_SESSION_OPEN = time(9, 15)


def resample_candles(
    candles: list[Candle],
    bucket_minutes: int,
    *,
    source_bucket_minutes: int = 1,
    session_open: time = DEFAULT_SESSION_OPEN,
    allow_partial: bool = False,
) -> list[Candle]:
    """Aggregate ``source_bucket_minutes``-sized candles into ``bucket_minutes``-sized OHLC bars.

    Groups by (calendar date, bucket index since session open), so this
    works across multiple days of input in one call. Input does not need to
    be pre-sorted. Raises ``ValueError`` on a duplicate timestamp for the
    same symbol (silently picking one would hide a data-quality problem).
    """
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    if source_bucket_minutes <= 0:
        raise ValueError("source_bucket_minutes must be positive")
    if bucket_minutes % source_bucket_minutes != 0:
        raise ValueError(
            f"bucket_minutes ({bucket_minutes}) must be a multiple of "
            f"source_bucket_minutes ({source_bucket_minutes})"
        )
    expected_members = bucket_minutes // source_bucket_minutes

    buckets: dict[tuple[str, object, int], list[Candle]] = defaultdict(list)
    seen: set[tuple[str, object]] = set()
    for candle in candles:
        key = (candle.symbol, candle.started_at)
        if key in seen:
            raise ValueError(f"duplicate candle timestamp for {candle.symbol} at {candle.started_at}")
        seen.add(key)
        minutes_since_open = (
            (candle.started_at.hour - session_open.hour) * 60
            + (candle.started_at.minute - session_open.minute)
        )
        if minutes_since_open < 0:
            raise ValueError(
                f"candle at {candle.started_at} starts before session_open {session_open}"
            )
        bucket_index = minutes_since_open // bucket_minutes
        buckets[(candle.symbol, candle.started_at.date(), bucket_index)].append(candle)

    result: list[Candle] = []
    for (symbol, day, bucket_index), members in buckets.items():
        if not allow_partial and len(members) != expected_members:
            continue
        members.sort(key=lambda item: item.started_at)
        result.append(
            Candle(
                symbol=symbol,
                started_at=members[0].started_at,
                open=members[0].open,
                high=max(item.high for item in members),
                low=min(item.low for item in members),
                close=members[-1].close,
            )
        )
    result.sort(key=lambda item: (item.symbol, item.started_at))
    return result
