"""Market-data-only Angel One adapter and provider contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .domain import Instrument, Quote


class MarketDataError(RuntimeError):
    """Raised when broker market data is missing or malformed."""


class MarketDataProvider(Protocol):
    def quote(self, instrument: Instrument, observed_at: datetime) -> Quote: ...
    def candles(self, instrument: Instrument, interval: str, from_at: datetime, to_at: datetime) -> list[list[object]]: ...


class AngelOneMarketData:
    """Use an authenticated SmartAPI SDK object for data, never orders."""

    def __init__(self, smart_api: object) -> None:
        self._smart_api = smart_api

    def quote(self, instrument: Instrument, observed_at: datetime) -> Quote:
        method = getattr(self._smart_api, "ltpData", None)
        if not callable(method):
            raise MarketDataError("SmartAPI object does not provide ltpData")
        response = method(instrument.exchange, instrument.symbol, instrument.token)
        data = response.get("data") if isinstance(response, dict) else None
        try:
            price = float(data["ltp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("Angel One returned no valid LTP") from exc
        return Quote(instrument.symbol, price, observed_at)

    def candles(self, instrument: Instrument, interval: str, from_at: datetime, to_at: datetime) -> list[list[object]]:
        method = getattr(self._smart_api, "getCandleData", None)
        if not callable(method):
            raise MarketDataError("SmartAPI object does not provide getCandleData")
        response = method(
            {
                "exchange": instrument.exchange,
                "symboltoken": instrument.token,
                "interval": interval,
                "fromdate": from_at.strftime("%Y-%m-%d %H:%M"),
                "todate": to_at.strftime("%Y-%m-%d %H:%M"),
            }
        )
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            raise MarketDataError("Angel One returned no candle list")
        return data
