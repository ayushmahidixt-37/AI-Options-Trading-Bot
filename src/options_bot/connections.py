"""Safe Angel One market-data and alert-only Telegram connections."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

import pyotp

from .config import Settings
from .credentials import load_credentials
from .notifications import TelegramNotifier

NIFTY_EXCHANGE = "NSE"
NIFTY_SYMBOL = "Nifty 50"
NIFTY_TOKEN = "99926000"


class ConnectionActionError(RuntimeError):
    """Raised with a display-safe message when an external action fails."""


@dataclass(frozen=True)
class ConnectionSnapshot:
    angel_status: str = "not connected"
    angel_client: str = "not configured"
    telegram_status: str = "not tested"
    nifty_price: float | None = None
    quote_observed_at: datetime | None = None
    quote_error: str | None = None
    last_message: str | None = None
    angel_error: str | None = None


def _smart_api_factory(api_key: str) -> object:
    from SmartApi import SmartConnect

    return SmartConnect(api_key)


def _masked(value: str) -> str:
    if not value:
        return "not configured"
    if len(value) <= 4:
        return "•" * len(value)
    return f"{'•' * (len(value) - 4)}{value[-4:]}"


def _safe_detail(value: object, secrets: tuple[str, ...]) -> str:
    detail = " ".join(str(value).split())
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "[redacted]")
    return detail[:180]


def _rejection_detail(response: object, secrets: tuple[str, ...]) -> str:
    if not isinstance(response, dict):
        return "Angel One returned an unexpected login response"
    error_code = _safe_detail(response.get("errorcode", ""), secrets)
    message = _safe_detail(response.get("message", ""), secrets)
    parts = [part for part in (error_code, message) if part]
    return " · ".join(parts) or "Angel One rejected the login without an error description"


class ConnectionManager:
    """Own authenticated data-only sessions without exposing credential values."""

    def __init__(
        self,
        settings: Settings,
        *,
        smart_api_factory: Callable[[str], object] = _smart_api_factory,
        totp_factory: Callable[[str], str] | None = None,
        notifier_factory: Callable[[str, str], TelegramNotifier] = TelegramNotifier,
    ) -> None:
        self._settings = settings
        self._smart_api_factory = smart_api_factory
        self._totp_factory = totp_factory or (lambda secret: pyotp.TOTP(secret).now())
        self._notifier_factory = notifier_factory
        self._smart_api: object | None = None
        self._lock = threading.RLock()
        self._snapshot = ConnectionSnapshot()

    def snapshot(self) -> ConnectionSnapshot:
        with self._lock:
            return self._snapshot

    def connect_angel(self) -> ConnectionSnapshot:
        credentials = load_credentials(self._settings.credentials_path)
        required = ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET")
        if any(not credentials.get(name, "").strip() for name in required):
            raise ConnectionActionError("Angel One credentials are incomplete")
        client_code = credentials["ANGEL_CLIENT_CODE"].strip()
        secret_values = tuple(credentials.get(name, "").strip() for name in required)
        try:
            smart_api = self._smart_api_factory(credentials["ANGEL_API_KEY"].strip())
            totp = self._totp_factory(credentials["ANGEL_TOTP_SECRET"].strip())
            response = getattr(smart_api, "generateSession")(
                client_code,
                credentials["ANGEL_PASSWORD"].strip(),
                totp,
            )
        except Exception as exc:
            detail = _safe_detail(f"{type(exc).__name__}: {exc}", secret_values)
            message = f"Angel One connection failed · {detail}"
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="connection failed",
                    angel_client=_masked(client_code),
                    angel_error=detail,
                    last_message=message,
                )
            raise ConnectionActionError(message) from exc
        if not isinstance(response, dict) or response.get("status") is False or not response.get("data"):
            detail = _rejection_detail(response, secret_values)
            message = f"Angel One rejected the login · {detail}"
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="connection failed",
                    angel_client=_masked(client_code),
                    angel_error=detail,
                    last_message=message,
                )
            raise ConnectionActionError(message)
        with self._lock:
            self._smart_api = smart_api
            self._snapshot = replace(
                self._snapshot,
                angel_status="connected",
                angel_client=_masked(client_code),
                angel_error=None,
                last_message="Angel One connected in market-data-only mode",
            )
            return self._snapshot

    def refresh_nifty(self) -> ConnectionSnapshot:
        with self._lock:
            smart_api = self._smart_api
        if smart_api is None:
            raise ConnectionActionError("Connect Angel One before refreshing NIFTY")
        credentials = load_credentials(self._settings.credentials_path)
        secret_values = tuple(value.strip() for value in credentials.values() if value.strip())
        try:
            response = getattr(smart_api, "ltpData")(NIFTY_EXCHANGE, NIFTY_SYMBOL, NIFTY_TOKEN)
        except Exception as exc:
            detail = _safe_detail(f"{type(exc).__name__}: {exc}", secret_values)
            message = f"NIFTY quote request failed · {detail}"
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="quote failed",
                    quote_error=detail,
                    last_message=message,
                )
            raise ConnectionActionError(message) from exc
        data = response.get("data") if isinstance(response, dict) else None
        try:
            price = float(data["ltp"])
            if price <= 0:
                raise ValueError("non-positive quote")
        except (KeyError, TypeError, ValueError) as exc:
            detail = _rejection_detail(response, secret_values)
            message = f"NIFTY quote refresh failed · {detail}"
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="quote failed",
                    quote_error=detail,
                    last_message=message,
                )
            raise ConnectionActionError(message) from exc
        now = datetime.now(self._settings.timezone)
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                angel_status="connected",
                nifty_price=price,
                quote_observed_at=now,
                quote_error=None,
                last_message="NIFTY quote refreshed",
            )
            return self._snapshot

    def test_telegram(self) -> ConnectionSnapshot:
        credentials = load_credentials(self._settings.credentials_path)
        token = credentials.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = credentials.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            raise ConnectionActionError("Telegram credentials are incomplete")
        try:
            self._notifier_factory(token, chat_id).send(
                "AI Options Trading Bot: Telegram alerts connected. Paper mode only; no trade was placed."
            )
        except Exception as exc:
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    telegram_status="test failed",
                    last_message="Telegram test failed",
                )
            raise ConnectionActionError("Telegram test failed") from exc
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                telegram_status="connected",
                last_message="Telegram test alert sent",
            )
            return self._snapshot
