"""Read-only DhanHQ historical option data client (expired-options rolling endpoint).

This module fetches only historical, already-expired NIFTY option data for
offline backtesting. It has no order-placement capability and must never be
used for anything beyond historical data retrieval.

Two verified quirks of Dhan's ``/charts/rollingoption`` endpoint shape this
module (see BACKTEST_FINDINGS.md's 2026-08-23 DhanHQ entry for the full
investigation):

1. The ``strike`` request parameter ("ATM", "ATM+1", ...) is *relative to a
   live-recomputed ATM*, re-evaluated every single minute -- a single label
   whipsaws between adjacent absolute strikes all day as spot ticks around the
   midpoint. It is NOT a stable single-contract series. Every response point
   does carry the real *absolute* strike though, so ``dhan_ingest.py``
   reconstructs a genuine fixed-strike series by re-grouping a wide band of
   labels (ATM-10..ATM+10) by that absolute value instead of trusting any one
   label's series directly.
2. ``expiryCode=0`` ("current/near expiry" per the docs) is rejected outright
   by the live API ("expiryCode is required") -- a real server-side bug, not
   a usage error. ``expiryCode=1`` is the only expiry-code value confirmed to
   work, and resolves to "the nearest expiry for the requested date range" in
   practice. Because expiries roll over (weekly, ~every 7 days), any request
   spanning a rollover boundary would silently splice two different
   contracts' data together -- so every call here must be scoped to fall
   entirely within one real expiry cycle. ``dhan_ingest.py`` is responsible
   for that scoping; this module does not enforce it.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import certifi

IST = ZoneInfo("Asia/Kolkata")

DHAN_ROLLING_OPTION_URL = "https://api.dhan.co/v2/charts/rollingoption"
DHAN_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
NIFTY_SECURITY_ID = 13
ONLY_WORKING_EXPIRY_CODE = 1  # expiryCode=0 is rejected by the live API; see module docstring.

# NIFTY lot size by the date a contract expires, per NSE circulars. Verified
# 2026-08-24 against primary/broker reporting of the actual NSE circulars
# (see BACKTEST_FINDINGS.md) -- required because the pre-Oct-2024 backfill
# this module exists for spans the 2024-04-26 lot-size change, and live/paper
# trading spans the 2026-01-06 change.
#
# The 75->65 change was originally (incorrectly) recorded here as effective
# 2025-10-28. Corrected 2026-08-24: the NSE circular revising it was issued
# 2025-11-28 and only took effect from the 2026-01-06 weekly expiry (monthly
# contracts from the 2026-01-27 expiry) -- contracts expiring before that
# date kept lot size 75 regardless of when the circular was announced. There
# is no evidence of any further change since (no NSE circular or broker
# reporting found for a 65->25 move); 65 remains the lot size current as of
# 2026-08-24.
_LOT_SIZE_CHANGES: tuple[tuple[date, int], ...] = (
    (date(1900, 1, 1), 50),
    (date(2024, 4, 26), 25),
    (date(2024, 11, 20), 75),
    (date(2026, 1, 6), 65),
)


def nifty_lot_size(as_of: date) -> int:
    """The real NIFTY lot size in effect on ``as_of`` (a contract's expiry date)."""
    size = _LOT_SIZE_CHANGES[0][1]
    for effective_from, value in _LOT_SIZE_CHANGES:
        if as_of >= effective_from:
            size = value
    return size


class DhanDataError(RuntimeError):
    """Raised when DhanHQ historical data is missing, malformed, or unauthorized."""


@dataclass(frozen=True)
class DhanRollingPoint:
    started_at: datetime
    strike: float
    open: float
    high: float | None
    low: float | None
    close: float
    volume: float | None
    open_interest: float | None
    implied_volatility: float | None = None


@dataclass(frozen=True)
class DhanIndexPoint:
    started_at: datetime
    open: float
    high: float
    low: float
    close: float


class DhanClient:
    """Thin wrapper around DhanHQ's expired-options rolling-data endpoint."""

    def __init__(self, access_token: str, *, timeout_seconds: int = 45) -> None:
        if not access_token:
            raise ValueError("access_token must not be empty")
        self._access_token = access_token
        self._timeout = timeout_seconds

    def fetch_rolling_option(
        self,
        *,
        strike_label: str,
        option_type: str,
        expiry_flag: str,
        from_date: date,
        to_date: date,
        security_id: int = NIFTY_SECURITY_ID,
    ) -> list[DhanRollingPoint]:
        if option_type not in {"CALL", "PUT"}:
            raise ValueError("option_type must be 'CALL' or 'PUT'")
        body = {
            "exchangeSegment": "NSE_FNO",
            "interval": "1",
            "securityId": security_id,
            "instrument": "OPTIDX",
            "expiryFlag": expiry_flag,
            "expiryCode": ONLY_WORKING_EXPIRY_CODE,
            "strike": strike_label,
            "drvOptionType": option_type,
            "requiredData": ["open", "high", "low", "close", "volume", "oi", "iv", "timestamp", "strike"],
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        }
        request = urllib.request.Request(
            DHAN_ROLLING_OPTION_URL,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "access-token": self._access_token,
            },
        )
        ctx = ssl.create_default_context(cafile=certifi.where())
        try:
            with urllib.request.urlopen(request, context=ctx, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise DhanDataError(f"Dhan rolling-option request failed: HTTP {exc.code}: {body_text[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DhanDataError(f"Dhan rolling-option request failed: {exc}") from exc

        side_key = "ce" if option_type == "CALL" else "pe"
        side = payload.get("data", {}).get(side_key)
        if not side or not side.get("timestamp"):
            return []
        n = len(side["timestamp"])
        highs = side.get("high") or [None] * n
        lows = side.get("low") or [None] * n
        volumes = side.get("volume") or [None] * n
        ois = side.get("oi") or [None] * n
        ivs = side.get("iv") or [None] * n
        try:
            return [
                DhanRollingPoint(
                    started_at=datetime.fromtimestamp(t, tz=IST),
                    strike=float(s),
                    open=float(o),
                    high=float(h) if h is not None else None,
                    low=float(lo) if lo is not None else None,
                    close=float(c),
                    volume=float(v) if v is not None else None,
                    open_interest=float(oi) if oi is not None else None,
                    implied_volatility=float(iv) if iv is not None else None,
                )
                for t, s, o, h, lo, c, v, oi, iv in zip(
                    side["timestamp"], side["strike"], side["open"], highs, lows, side["close"], volumes, ois, ivs
                )
            ]
        except (TypeError, ValueError, KeyError) as exc:
            raise DhanDataError(f"Malformed Dhan rolling-option response: {exc}") from exc

    def fetch_index_intraday(
        self,
        *,
        from_date: datetime,
        to_date: datetime,
        security_id: int = NIFTY_SECURITY_ID,
    ) -> list[DhanIndexPoint]:
        """Fetch real NIFTY index (spot) 1-minute candles -- a plain single
        instrument, unlike the ATM-relative options endpoint, so no
        reconstruction is needed. Verified 2026-08-24 to go back to at least
        2020-08-03 (real values, e.g. NIFTY close ~11,027 that day) but not
        to 2015 -- matches the same real historical ceiling found for the
        options data. Dhan documents a 90-day-per-call limit for intraday
        data; callers are responsible for chunking.
        """
        body = {
            "securityId": str(security_id),
            "exchangeSegment": "IDX_I",
            "instrument": "INDEX",
            "interval": "1",
            "oi": False,
            "fromDate": from_date.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": to_date.strftime("%Y-%m-%d %H:%M:%S"),
        }
        request = urllib.request.Request(
            DHAN_INTRADAY_URL,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "access-token": self._access_token,
            },
        )
        ctx = ssl.create_default_context(cafile=certifi.where())
        try:
            with urllib.request.urlopen(request, context=ctx, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise DhanDataError(f"Dhan intraday request failed: HTTP {exc.code}: {body_text[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DhanDataError(f"Dhan intraday request failed: {exc}") from exc

        if not payload.get("timestamp"):
            return []
        try:
            return [
                DhanIndexPoint(
                    started_at=datetime.fromtimestamp(t, tz=IST),
                    open=float(o), high=float(h), low=float(lo), close=float(c),
                )
                for t, o, h, lo, c in zip(
                    payload["timestamp"], payload["open"], payload["high"], payload["low"], payload["close"]
                )
            ]
        except (TypeError, ValueError, KeyError) as exc:
            raise DhanDataError(f"Malformed Dhan intraday response: {exc}") from exc
