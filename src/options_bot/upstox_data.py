"""Read-only Upstox historical/expired-option data client.

This module fetches instrument metadata and OHLC(+open interest) candles for
offline backtesting only. It has no order-placement capability and must never
be used for anything beyond historical data retrieval.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

UPSTOX_BASE_URL = "https://api.upstox.com"

EXPIRED_CANDLE_INTERVALS = frozenset(
    {"1minute", "3minute", "5minute", "15minute", "30minute", "day"}
)

# transport(method, url, headers, timeout_seconds) -> (status_code, response_body_text)
Transport = Callable[[str, str, dict[str, str], int], tuple[int, str]]


class UpstoxDataError(RuntimeError):
    """Raised when Upstox historical data is missing, malformed, or unauthorized."""


@dataclass(frozen=True)
class UpstoxCandle:
    """One OHLC(+open interest) candle sourced from Upstox, never from Angel."""

    symbol: str
    started_at: datetime
    open: float
    high: float
    low: float
    close: float
    open_interest: float | None = None


def parse_candle_row(symbol: str, row: list[object]) -> UpstoxCandle:
    """Convert one raw ``[timestamp, o, h, l, c, volume, oi]`` Upstox row."""
    if len(row) < 5:
        raise UpstoxDataError(f"Malformed Upstox candle row: {row!r}")
    timestamp, open_, high, low, close = row[0], row[1], row[2], row[3], row[4]
    open_interest = None
    if len(row) > 6 and row[6] is not None:
        open_interest = float(row[6])
    try:
        return UpstoxCandle(
            symbol=symbol,
            started_at=datetime.fromisoformat(str(timestamp)),
            open=float(open_),
            high=float(high),
            low=float(low),
            close=float(close),
            open_interest=open_interest,
        )
    except (TypeError, ValueError) as exc:
        raise UpstoxDataError(f"Malformed Upstox candle row: {row!r}") from exc


def _default_transport(method: str, url: str, headers: dict[str, str], timeout_seconds: int) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


class UpstoxClient:
    """Thin, mockable wrapper around Upstox's read-only historical data APIs."""

    def __init__(
        self,
        access_token: str,
        timeout_seconds: int = 10,
        base_url: str = UPSTOX_BASE_URL,
        transport: Transport | None = None,
    ) -> None:
        if not access_token:
            raise UpstoxDataError("Upstox access token is required")
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _default_transport

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        status, body = self._transport("GET", url, self._headers(), self._timeout_seconds)
        if status == 401:
            raise UpstoxDataError(
                "Upstox session expired or invalid — renew UPSTOX_ACCESS_TOKEN"
            )
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise UpstoxDataError(f"Upstox returned malformed JSON (status {status})") from exc
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and any(
            isinstance(item, dict) and item.get("errorCode") == "UDAPI1149" for item in errors
        ):
            raise UpstoxDataError(
                "This Upstox endpoint requires an active Upstox Plus subscription"
            )
        if status >= 400 or (isinstance(payload, dict) and payload.get("status") == "error"):
            raise UpstoxDataError(f"Upstox request failed (status {status}): {body[:300]}")
        return payload

    def search_instruments(
        self, query: str, exchange: str = "NSE", segment: str = "INDEX"
    ) -> list[dict[str, object]]:
        payload = self._get(
            "/v2/instruments/search",
            {"query": query, "exchanges": exchange, "segments": segment},
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise UpstoxDataError("Upstox instrument search returned no data")
        return data

    def get_expiries(self, instrument_key: str) -> list[date]:
        payload = self._get(
            "/v2/expired-instruments/expiries", {"instrument_key": instrument_key}
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise UpstoxDataError("Upstox expiries lookup returned no data")
        return [date.fromisoformat(item) for item in data]

    def get_expired_option_contracts(
        self, instrument_key: str, expiry: date
    ) -> list[dict[str, object]]:
        payload = self._get(
            "/v2/expired-instruments/option/contract",
            {"instrument_key": instrument_key, "expiry_date": expiry.isoformat()},
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise UpstoxDataError("Upstox expired option contracts lookup returned no data")
        return data

    def get_expired_historical_candles(
        self,
        expired_instrument_key: str,
        from_date: date,
        to_date: date,
        interval: str = "5minute",
    ) -> list[list[object]]:
        if interval not in EXPIRED_CANDLE_INTERVALS:
            raise UpstoxDataError(f"Unsupported expired-candle interval: {interval}")
        encoded_key = urllib.parse.quote(expired_instrument_key, safe="")
        path = (
            f"/v2/expired-instruments/historical-candle/{encoded_key}/{interval}/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )
        payload = self._get(path)
        return _extract_candles(payload, "Upstox expired historical candle lookup")

    def get_historical_candles_v3(
        self,
        instrument_key: str,
        unit: str,
        interval: str,
        from_date: date,
        to_date: date,
    ) -> list[list[object]]:
        encoded_key = urllib.parse.quote(instrument_key, safe="")
        path = (
            f"/v3/historical-candle/{encoded_key}/{unit}/{interval}/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )
        payload = self._get(path)
        return _extract_candles(payload, "Upstox historical candle lookup")


def _extract_candles(payload: dict[str, object], description: str) -> list[list[object]]:
    data = payload.get("data")
    candles = data.get("candles") if isinstance(data, dict) else None
    if not isinstance(candles, list):
        raise UpstoxDataError(f"{description} returned no data")
    return candles
