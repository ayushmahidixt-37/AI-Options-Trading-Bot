from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from options_bot.candles import CandleStore


def test_tick_aggregation_closes_previous_minute() -> None:
    store = CandleStore(history_size=2)
    start = datetime(2026, 8, 3, 10, 0, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert store.update("NIFTY", 100, start) is None
    assert store.update("NIFTY", 105, start + timedelta(seconds=10)) is None
    assert store.update("NIFTY", 98, start + timedelta(seconds=20)) is None
    closed = store.update("NIFTY", 101, start + timedelta(minutes=1))
    assert (closed.open, closed.high, closed.low, closed.close) == (100, 105, 98, 98)
    assert store.closed("NIFTY", 1) == [closed]


def test_out_of_order_and_naive_ticks_are_rejected() -> None:
    store = CandleStore()
    aware = datetime(2026, 8, 3, 10, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
    store.update("NIFTY", 100, aware)
    with pytest.raises(ValueError, match="out-of-order"):
        store.update("NIFTY", 100, aware - timedelta(minutes=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        store.update("NIFTY", 100, datetime(2026, 8, 3, 10, 2))


def test_same_minute_out_of_order_tick_is_rejected() -> None:
    store = CandleStore()
    start = datetime(2026, 8, 3, 10, 0, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    store.update("NIFTY", 100, start)
    with pytest.raises(ValueError, match="out-of-order"):
        store.update("NIFTY", 101, start - timedelta(seconds=10))
