"""Thread-safe tick-to-closed-candle aggregation."""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    symbol: str
    started_at: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class _MutableCandle:
    symbol: str
    started_at: datetime
    open: float
    high: float
    low: float
    close: float
    last_observed_at: datetime

    def frozen(self) -> Candle:
        return Candle(
            symbol=self.symbol,
            started_at=self.started_at,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
        )


class CandleStore:
    """Aggregate one-minute candles without nested-lock deadlocks."""

    def __init__(self, history_size: int = 500) -> None:
        if history_size <= 0:
            raise ValueError("history_size must be positive")
        self._lock = threading.Lock()
        self._current: dict[str, _MutableCandle] = {}
        self._closed: dict[str, deque[Candle]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )

    def update(self, symbol: str, price: float, observed_at: datetime) -> Candle | None:
        if price <= 0:
            raise ValueError("price must be positive")
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        minute = observed_at.replace(second=0, microsecond=0)
        with self._lock:
            current = self._current.get(symbol)
            if current is None:
                self._current[symbol] = _MutableCandle(symbol, minute, price, price, price, price, observed_at)
                return None
            if minute < current.started_at:
                raise ValueError("out-of-order tick")
            if minute == current.started_at:
                if observed_at < current.last_observed_at:
                    raise ValueError("out-of-order tick")
                current.high = max(current.high, price)
                current.low = min(current.low, price)
                current.close = price
                current.last_observed_at = observed_at
                return None
            closed = current.frozen()
            self._closed[symbol].append(closed)
            self._current[symbol] = _MutableCandle(symbol, minute, price, price, price, price, observed_at)
            return closed

    def closed(self, symbol: str, count: int) -> list[Candle]:
        if count <= 0:
            return []
        with self._lock:
            return list(self._closed[symbol])[-count:]
