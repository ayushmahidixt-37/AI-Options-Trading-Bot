"""Core paper-trading value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Instrument:
    symbol: str
    token: str
    exchange: str
    underlying: str
    option_type: str
    lot_size: int

    def __post_init__(self) -> None:
        if self.option_type not in {"CE", "PE"}:
            raise ValueError("Only CE and PE instruments are supported")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("quote price must be positive")
        if self.observed_at.tzinfo is None:
            raise ValueError("quote timestamp must be timezone-aware")


@dataclass(frozen=True)
class PaperOrderRequest:
    instrument: Instrument
    lots: int
    quote: Quote
    stop_price: float
    strategy: str
    reason: str

    @property
    def units(self) -> int:
        return self.lots * self.instrument.lot_size

    @property
    def risk_at_stop(self) -> float:
        return max(0.0, self.quote.price - self.stop_price) * self.units
