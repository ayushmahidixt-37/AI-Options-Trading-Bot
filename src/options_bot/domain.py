"""Core paper-trading value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import date


@dataclass(frozen=True)
class Instrument:
    symbol: str
    token: str
    exchange: str
    underlying: str
    option_type: str
    lot_size: int
    expiry: date | None = None
    strike: float | None = None

    def __post_init__(self) -> None:
        if self.option_type not in {"CE", "PE"}:
            raise ValueError("Only CE and PE instruments are supported")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if self.strike is not None and self.strike <= 0:
            raise ValueError("strike must be positive")


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
    target_price: float | None = None
    side: str = "BUY"
    trade_group_id: str | None = None

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be 'BUY' or 'SELL'")

    @property
    def units(self) -> int:
        return self.lots * self.instrument.lot_size

    @property
    def risk_at_stop(self) -> float:
        """Worst-case loss if the stop fires, direction-aware.

        A long (BUY) position loses as price falls *below* the stop; a
        short (SELL) position -- added 2026-08-25 for the short strangle --
        loses as price rises *above* it, since the position profits from
        the option decaying toward zero and losses accumulate the other way.
        """
        if self.side == "SELL":
            return max(0.0, self.stop_price - self.quote.price) * self.units
        return max(0.0, self.quote.price - self.stop_price) * self.units
