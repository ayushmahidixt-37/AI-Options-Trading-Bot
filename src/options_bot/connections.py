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
    last_message: str | None = None


def _smart_api_factory(api_key: str) -> object:
    from SmartApi import SmartConnect

    return SmartConnect(api_key)


def _masked(value: str) -> str:
    if not value:
        return "not configured"
    if len(value) <= 4:
        return "•" * len(value)
    return f"{'•' * (len(value) - 4)}{value[-4:]}"


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
        try:
            smart_api = self._smart_api_factory(credentials["ANGEL_API_KEY"].strip())
            totp = self._totp_factory(credentials["ANGEL_TOTP_SECRET"].strip())
            response = getattr(smart_api, "generateSession")(
                client_code,
                credentials["ANGEL_PASSWORD"].strip(),
                totp,
            )
        except Exception as exc:
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="connection failed",
                    angel_client=_masked(client_code),
                    last_message="Angel One connection failed",
                )
            raise ConnectionActionError("Angel One connection failed") from exc
        if not isinstance(response, dict) or response.get("status") is False or not response.get("data"):
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="connection failed",
                    angel_client=_masked(client_code),
                    last_message="Angel One rejected the login",
                )
            raise ConnectionActionError("Angel One rejected the login")
        with self._lock:
            self._smart_api = smart_api
            self._snapshot = replace(
                self._snapshot,
                angel_status="connected",
                angel_client=_masked(client_code),
                last_message="Angel One connected in market-data-only mode",
            )
            return self._snapshot

    def refresh_nifty(self) -> ConnectionSnapshot:
        with self._lock:
            smart_api = self._smart_api
        if smart_api is None:
            raise ConnectionActionError("Connect Angel One before refreshing NIFTY")
        try:
            response = getattr(smart_api, "ltpData")(NIFTY_EXCHANGE, NIFTY_SYMBOL, NIFTY_TOKEN)
            data = response.get("data") if isinstance(response, dict) else None
            price = float(data["ltp"])
            if price <= 0:
                raise ValueError("non-positive quote")
        except Exception as exc:
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="quote failed",
                    last_message="NIFTY quote refresh failed",
                )
            raise ConnectionActionError("NIFTY quote refresh failed") from exc
        now = datetime.now(self._settings.timezone)
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                angel_status="connected",
                nifty_price=price,
                quote_observed_at=now,
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
