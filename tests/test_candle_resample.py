from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from options_bot.candle_resample import resample_candles
from options_bot.candles import Candle

IST = ZoneInfo("Asia/Kolkata")


def _minute_candles(count: int, start: datetime, base: float = 100.0) -> list[Candle]:
    return [
        Candle(
            "NIFTY", start + timedelta(minutes=i),
            open=base + i, high=base + i + 1, low=base + i - 1, close=base + i + 0.5,
        )
        for i in range(count)
    ]


def test_five_minute_bucket_aggregates_ohlc_correctly() -> None:
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = _minute_candles(5, start)  # exactly one complete 5-min bucket

    result = resample_candles(candles, bucket_minutes=5)

    assert len(result) == 1
    bar = result[0]
    assert bar.started_at == start
    assert bar.open == candles[0].open
    assert bar.close == candles[-1].close
    assert bar.high == max(c.high for c in candles)
    assert bar.low == min(c.low for c in candles)


def test_incomplete_trailing_bucket_is_dropped_by_default() -> None:
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = _minute_candles(7, start)  # one complete 5-min bucket + 2 leftover minutes

    result = resample_candles(candles, bucket_minutes=5)

    assert len(result) == 1  # the trailing 2-minute partial bucket must not appear


def test_allow_partial_keeps_the_incomplete_trailing_bucket() -> None:
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = _minute_candles(7, start)

    result = resample_candles(candles, bucket_minutes=5, allow_partial=True)

    assert len(result) == 2
    assert result[1].started_at == start + timedelta(minutes=5)


def test_buckets_align_to_session_open_not_clock_hour() -> None:
    # 09:15 session open, 10-minute buckets: 09:15-09:24 is bucket 0,
    # 09:25-09:34 is bucket 1 -- NOT aligned to :10/:20/:30 clock minutes.
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = _minute_candles(20, start)

    result = resample_candles(candles, bucket_minutes=10)

    assert len(result) == 2
    assert result[0].started_at == start
    assert result[1].started_at == start + timedelta(minutes=10)


def test_multi_day_input_is_grouped_by_calendar_date() -> None:
    day1 = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    day2 = datetime(2026, 8, 4, 9, 15, tzinfo=IST)
    candles = _minute_candles(5, day1) + _minute_candles(5, day2, base=200.0)

    result = resample_candles(candles, bucket_minutes=5)

    assert len(result) == 2
    assert result[0].started_at.date() == day1.date()
    assert result[1].started_at.date() == day2.date()
    assert result[1].open == 200.0


def test_duplicate_timestamp_raises() -> None:
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = _minute_candles(5, start) + [_minute_candles(1, start)[0]]

    with pytest.raises(ValueError):
        resample_candles(candles, bucket_minutes=5)


def test_candle_before_session_open_raises() -> None:
    before_open = datetime(2026, 8, 3, 9, 0, tzinfo=IST)
    candles = [Candle("NIFTY", before_open, 100, 101, 99, 100.5)]

    with pytest.raises(ValueError):
        resample_candles(candles, bucket_minutes=5)


def test_different_symbols_are_never_mixed_into_the_same_bucket() -> None:
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    ce_candles = _minute_candles(5, start, base=100.0)
    pe_candles = [Candle("NIFTY_PE", c.started_at, c.open, c.high, c.low, c.close) for c in ce_candles]

    result = resample_candles(ce_candles + pe_candles, bucket_minutes=5)

    assert len(result) == 2
    assert {bar.symbol for bar in result} == {"NIFTY", "NIFTY_PE"}


def test_bucket_size_that_does_not_evenly_divide_the_session_drops_the_tail() -> None:
    # A 375-minute session (09:15-15:30) is not a multiple of 10 minutes --
    # the last partial bucket of a real session must be dropped, not kept.
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = _minute_candles(375, start)

    result = resample_candles(candles, bucket_minutes=10)

    assert len(result) == 37  # floor(375/10); the trailing 5-minute remainder is dropped
    assert result[-1].started_at == start + timedelta(minutes=36 * 10)
