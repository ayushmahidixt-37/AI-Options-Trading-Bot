"""Safe Angel One market-data and alert-only Telegram connections."""

from __future__ import annotations

import threading
import logging
import json
import time as clock
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, tzinfo
from typing import Callable
from urllib.request import Request, urlopen

import pyotp

from .candles import Candle
from .config import Settings
from .credentials import load_credentials
from .domain import Instrument, Quote
from .indicators import atr, ema, rsi
from .instruments import normalize_instruments
from .market_archive import MarketArchive
from .notifications import TelegramNotifier
from .strategy import MomentumStrategy

NIFTY_EXCHANGE = "NSE"
NIFTY_SYMBOL = "Nifty 50"
NIFTY_TOKEN = "99926000"
NIFTY_CANDLE_INTERVAL = "FIVE_MINUTE"
NIFTY_CANDLE_LIMIT = 100
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)


class ConnectionActionError(RuntimeError):
    """Raised with a display-safe message when an external action fails."""


@dataclass(frozen=True)
class PaperTradeProposal:
    proposal_id: str
    instrument: Instrument
    quote: Quote
    stop_price: float
    direction: str
    confidence: float
    reason: str
    estimated_max_loss: float


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
    intelligence_status: str = "not loaded"
    intelligence_error: str | None = None
    market_status: str = "unknown"
    candle_count: int = 0
    latest_candle_at: datetime | None = None
    data_status: str = "not loaded"
    ema_fast: float | None = None
    ema_slow: float | None = None
    rsi_value: float | None = None
    atr_value: float | None = None
    signal_label: str = "NOT LOADED"
    signal_confidence: float | None = None
    signal_reason: str = "Load five-minute candles to calculate a read-only signal."
    monitor_status: str = "stopped"
    monitor_last_run_at: datetime | None = None
    monitor_error: str | None = None
    archive_status: str = "not initialized"
    archive_error: str | None = None
    archived_candles: int = 0
    archived_nifty_candles: int = 0
    archived_option_candles: int = 0
    archived_instruments: int = 0
    archive_bytes: int = 0
    archive_oldest_at: str | None = None
    archive_newest_at: str | None = None
    archive_gaps: int = 0
    nearest_expiry: str | None = None
    atm_strike: float | None = None
    option_archive_status: str = "not collected"
    option_contracts_tracked: int = 0
    option_archive_error: str | None = None


def _smart_api_factory(api_key: str) -> object:
    from SmartApi import SmartConnect
    from logzero import logger

    # SmartAPI logs full request headers (including X-PrivateKey) on failures.
    # Disable its global logger before any network request to keep secrets out of
    # Termux output and SDK-created log files; this application surfaces only
    # explicitly redacted diagnostics in the dashboard.
    logger.setLevel(logging.CRITICAL)
    logger.disabled = True
    return SmartConnect(api_key)


def _instrument_master_loader() -> list[dict[str, object]]:
    request = Request(INSTRUMENT_MASTER_URL, headers={"User-Agent": "options-bot/0.1"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError("Angel One instrument master is not a list")
    return payload


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
    error_code = _safe_detail(
        response.get("errorcode") or response.get("errorCode") or "", secrets
    )
    message = _safe_detail(response.get("message", ""), secrets)
    parts = [part for part in (error_code, message) if part]
    return " · ".join(parts) or "Angel One rejected the login without an error description"


def _quote_price(response: object) -> float:
    if not isinstance(response, dict):
        raise TypeError("quote response is not an object")
    data = response.get("data")
    if isinstance(data, dict) and "ltp" in data:
        price = float(data["ltp"])
    elif isinstance(data, dict) and isinstance(data.get("fetched"), list) and data["fetched"]:
        price = float(data["fetched"][0]["ltp"])
    else:
        raise KeyError("quote response has no LTP")
    if price <= 0:
        raise ValueError("quote is not positive")
    return price


def _parse_candle_timestamp(value: object, timezone: tzinfo) -> datetime:
    timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone)
    return timestamp.astimezone(timezone)


def _closed_five_minute_candles(
    rows: object,
    *,
    now: datetime,
    timezone: tzinfo,
    symbol: str = NIFTY_SYMBOL,
    limit: int = NIFTY_CANDLE_LIMIT,
) -> list[Candle]:
    if not isinstance(rows, list):
        raise ValueError("Angel One returned no candle list")
    bucket_minute = now.minute - now.minute % 5
    current_bucket = now.replace(minute=bucket_minute, second=0, microsecond=0)
    candles: list[Candle] = []
    previous: datetime | None = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            raise ValueError("Angel One returned a malformed candle")
        started_at = _parse_candle_timestamp(row[0], timezone)
        if previous is not None and started_at <= previous:
            raise ValueError("Angel One returned duplicate or out-of-order candles")
        previous = started_at
        if started_at >= current_bucket:
            continue
        open_price, high, low, close = (float(value) for value in row[1:5])
        if min(open_price, high, low, close) <= 0:
            raise ValueError("Angel One returned a non-positive candle price")
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise ValueError("Angel One returned inconsistent OHLC values")
        candles.append(Candle(symbol, started_at, open_price, high, low, close))
    return candles[-limit:]


class ConnectionManager:
    """Own authenticated data-only sessions without exposing credential values."""

    def __init__(
        self,
        settings: Settings,
        *,
        smart_api_factory: Callable[[str], object] = _smart_api_factory,
        totp_factory: Callable[[str], str] | None = None,
        notifier_factory: Callable[[str, str], TelegramNotifier] = TelegramNotifier,
        instrument_master_loader: Callable[[], list[dict[str, object]]] = (
            _instrument_master_loader
        ),
        market_archive: MarketArchive | None = None,
        request_pause: Callable[[float], None] = clock.sleep,
    ) -> None:
        self._settings = settings
        self._smart_api_factory = smart_api_factory
        self._totp_factory = totp_factory or (lambda secret: pyotp.TOTP(secret).now())
        self._notifier_factory = notifier_factory
        self._instrument_master_loader = instrument_master_loader
        self.archive = market_archive or MarketArchive(settings.data_dir / "market-data.sqlite3")
        self._request_pause = request_pause
        self.archive.initialize()
        self._smart_api: object | None = None
        self._lock = threading.RLock()
        self._snapshot = ConnectionSnapshot()
        self._last_alerted_signal: str | None = None
        self._last_intelligence_bucket: datetime | None = None
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._last_instrument_refresh_date = None
        self._last_option_archive_bucket = None
        self._last_backup_date = None
        self._paper_cycle: Callable[[datetime], object] | None = None
        self._refresh_archive_snapshot()

    def snapshot(self) -> ConnectionSnapshot:
        with self._lock:
            return self._snapshot

    def register_paper_cycle(self, callback: Callable[[datetime], object]) -> None:
        """Attach an exit-only paper monitor to the existing background worker."""
        self._paper_cycle = callback

    def _refresh_archive_snapshot(self) -> None:
        stats = self.archive.stats()
        with self._lock:
            spot = self._snapshot.nifty_price
        selection = self.archive.nearest_expiry_summary(
            datetime.now(self._settings.timezone).date(), spot
        )
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                archive_status="ready",
                archive_error=None,
                archived_candles=stats.candle_count,
                archived_nifty_candles=stats.nifty_candle_count,
                archived_option_candles=stats.option_candle_count,
                archived_instruments=stats.instrument_count,
                archive_bytes=stats.database_bytes,
                archive_oldest_at=stats.oldest_candle_at,
                archive_newest_at=stats.newest_candle_at,
                archive_gaps=stats.missing_five_minute_buckets,
                nearest_expiry=selection["nearest_expiry"],
                atm_strike=selection["atm_strike"],
            )

    def refresh_instrument_archive(
        self, observed_at: datetime | None = None
    ) -> ConnectionSnapshot:
        """Archive current NIFTY option metadata without requesting option prices."""
        now = (observed_at or datetime.now(self._settings.timezone)).astimezone(
            self._settings.timezone
        )
        try:
            rows = self._instrument_master_loader()
            instruments = [
                item
                for item in normalize_instruments(rows)
                if item.underlying == "NIFTY"
            ]
            saved = self.archive.save_instruments(instruments, now)
            self.archive.record_run(now, "success", 0, saved, "NIFTY instruments archived")
            self._last_instrument_refresh_date = now.date()
            self._refresh_archive_snapshot()
        except Exception as exc:
            detail = _safe_detail(f"{type(exc).__name__}: {exc}", ())
            self.archive.record_run(now, "failed", 0, 0, detail)
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    archive_status="instrument refresh failed",
                    archive_error=detail,
                )
            raise ConnectionActionError(f"Instrument archive refresh failed · {detail}") from exc
        return self.snapshot()

    def refresh_option_archive(
        self, observed_at: datetime | None = None, strike_band: int = 5
    ) -> ConnectionSnapshot:
        """Store closed candles for nearest-expiry NIFTY options around ATM."""
        now = (observed_at or datetime.now(self._settings.timezone)).astimezone(
            self._settings.timezone
        )
        bucket = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        with self._lock:
            smart_api = self._smart_api
            spot = self._snapshot.nifty_price
            if bucket == self._last_option_archive_bucket:
                return self._snapshot
        if smart_api is None or not spot:
            raise ConnectionActionError("NIFTY connection and spot are required")
        contracts = self.archive.select_near_atm_options(now.date(), spot, strike_band)
        if not contracts:
            raise ConnectionActionError("No current archived NIFTY options are available")

        saved = 0
        failures: list[str] = []
        for index, contract in enumerate(contracts):
            if index:
                self._request_pause(0.4)
            try:
                response = getattr(smart_api, "getCandleData")(
                    {
                        "exchange": contract.exchange,
                        "symboltoken": contract.token,
                        "interval": NIFTY_CANDLE_INTERVAL,
                        "fromdate": (now - timedelta(minutes=20)).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "todate": now.strftime("%Y-%m-%d %H:%M"),
                    }
                )
                if not isinstance(response, dict) or response.get("status") is False:
                    raise ValueError(_rejection_detail(response, ()))
                candles = _closed_five_minute_candles(
                    response.get("data"),
                    now=now,
                    timezone=self._settings.timezone,
                    symbol=contract.symbol,
                    limit=4,
                )
                saved += self.archive.save_candles(
                    candles,
                    token=contract.token,
                    exchange=contract.exchange,
                    timeframe=NIFTY_CANDLE_INTERVAL,
                    collected_at=now,
                )
            except Exception as exc:
                failures.append(f"{contract.symbol}: {_safe_detail(str(exc), ())}")

        status = "success" if not failures else "partial"
        detail = "; ".join(failures[:3]) or "ATM ±5 option candles archived"
        self.archive.record_run(now, status, saved, 0, detail)
        with self._lock:
            self._last_option_archive_bucket = bucket
            self._snapshot = replace(
                self._snapshot,
                option_archive_status=status,
                option_contracts_tracked=len(contracts),
                option_archive_error=detail if failures else None,
            )
        self._refresh_archive_snapshot()
        return self.snapshot()

    def create_paper_proposal(
        self, observed_at: datetime | None = None
    ) -> PaperTradeProposal:
        """Build a fresh, non-executing one-lot proposal from the current signal."""
        now = (observed_at or datetime.now(self._settings.timezone)).astimezone(
            self._settings.timezone
        )
        with self._lock:
            smart_api = self._smart_api
            spot = self._snapshot.nifty_price
            signal = self._snapshot.signal_label
            confidence = self._snapshot.signal_confidence
            reason = self._snapshot.signal_reason
            data_status = self._snapshot.data_status
        if smart_api is None or not spot:
            raise ConnectionActionError("Connect Angel One and load NIFTY spot first")
        if data_status != "fresh" or signal not in {"BULLISH", "BEARISH"}:
            raise ConnectionActionError("A fresh BULLISH or BEARISH signal is required")
        required_type = "CE" if signal == "BULLISH" else "PE"
        contracts = self.archive.select_near_atm_options(now.date(), spot, 0)
        instrument = next(
            (item for item in contracts if item.option_type == required_type), None
        )
        if instrument is None:
            raise ConnectionActionError("No matching ATM option is archived")
        quote = self.quote_instrument(instrument, now)
        price = quote.price
        expected_fill = price * (1 + self._settings.paper_slippage_bps / 10_000)
        risk_budget = self._settings.max_loss_per_trade * 0.8
        fees = 2 * self._settings.paper_fee_per_order
        stop_distance = max(0.01, (risk_budget - fees) / instrument.lot_size)
        stop = round(expected_fill - stop_distance, 2)
        if stop <= 0 or stop >= price:
            raise ConnectionActionError("Configured risk budget cannot produce a valid stop")
        estimated_loss = round((expected_fill - stop) * instrument.lot_size + fees, 2)
        return PaperTradeProposal(
            proposal_id=str(uuid.uuid4()),
            instrument=instrument,
            quote=quote,
            stop_price=stop,
            direction=signal,
            confidence=confidence or 0.0,
            reason=reason,
            estimated_max_loss=estimated_loss,
        )

    def quote_instrument(
        self, instrument: Instrument, observed_at: datetime | None = None
    ) -> Quote:
        """Fetch one display-safe market quote without exposing an order method."""
        now = (observed_at or datetime.now(self._settings.timezone)).astimezone(
            self._settings.timezone
        )
        with self._lock:
            smart_api = self._smart_api
        if smart_api is None:
            raise ConnectionActionError("Angel One is not connected")
        try:
            try:
                response = getattr(smart_api, "getMarketData")(
                    "LTP", {instrument.exchange: [instrument.token]}
                )
            except (AttributeError, TypeError, RuntimeError):
                response = getattr(smart_api, "ltpData")(
                    instrument.exchange, instrument.symbol, instrument.token
                )
            price = _quote_price(response)
        except Exception as exc:
            raise ConnectionActionError(
                f"Option quote failed · {_safe_detail(str(exc), ())}"
            ) from exc
        return Quote(instrument.symbol, price, now)

    def start_background_monitor(self, interval_seconds: float = 15) -> None:
        """Start one read-only market-data worker for this application process."""
        if interval_seconds <= 0:
            raise ValueError("Background monitor interval must be positive")
        with self._lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._monitor_stop.clear()
            self._snapshot = replace(
                self._snapshot,
                monitor_status="starting",
                monitor_error=None,
            )
            worker = threading.Thread(
                target=self._monitor_loop,
                args=(interval_seconds,),
                name="nifty-read-only-monitor",
                daemon=True,
            )
            self._monitor_thread = worker
            worker.start()

    def stop_background_monitor(self, timeout_seconds: float = 5) -> None:
        """Stop the worker without leaving a non-daemon shutdown dependency."""
        self._monitor_stop.set()
        with self._lock:
            worker = self._monitor_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout_seconds)
        with self._lock:
            self._monitor_thread = None
            self._snapshot = replace(self._snapshot, monitor_status="stopped")

    def run_background_cycle(self, observed_at: datetime | None = None) -> ConnectionSnapshot:
        """Run one safe cycle; useful for the worker and deterministic checks."""
        now = (observed_at or datetime.now(self._settings.timezone)).astimezone(
            self._settings.timezone
        )
        with self._lock:
            connected = self._smart_api is not None
        if not connected:
            self.connect_angel()
        self.refresh_nifty()
        if self._last_instrument_refresh_date != now.date():
            self._last_instrument_refresh_date = now.date()
            try:
                self.refresh_instrument_archive(now)
            except ConnectionActionError:
                pass
        self.refresh_intelligence_if_due(now)
        if now.weekday() < 5 and MARKET_OPEN <= now.time() < MARKET_CLOSE:
            try:
                self.refresh_option_archive(now)
            except ConnectionActionError as exc:
                with self._lock:
                    self._snapshot = replace(
                        self._snapshot,
                        option_archive_status="waiting",
                        option_archive_error=str(exc),
                    )
        if now.time() >= self._settings.force_exit and self._last_backup_date != now.date():
            target = (
                self._settings.data_dir
                / "backups"
                / f"market-data-{now.strftime('%Y%m%d')}.sqlite3"
            )
            self.archive.backup(target)
            self._last_backup_date = now.date()
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                monitor_status="running",
                monitor_last_run_at=now,
                monitor_error=None,
            )
            return self._snapshot

    def _monitor_loop(self, interval_seconds: float) -> None:
        while not self._monitor_stop.is_set():
            now = datetime.now(self._settings.timezone)
            try:
                self.run_background_cycle(now)
            except (ConnectionActionError, FileNotFoundError, ValueError) as exc:
                with self._lock:
                    self._snapshot = replace(
                        self._snapshot,
                        monitor_status="retrying",
                        monitor_error=_safe_detail(str(exc), ()),
                    )
            finally:
                if self._paper_cycle is not None:
                    try:
                        self._paper_cycle(now)
                    except Exception as exc:
                        with self._lock:
                            self._snapshot = replace(
                                self._snapshot,
                                monitor_error=f"Paper exit monitor failed: {_safe_detail(str(exc), ())}",
                            )
            self._monitor_stop.wait(interval_seconds)

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
            self._last_intelligence_bucket = None
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
        response: object = {}
        try:
            response = getattr(smart_api, "getMarketData")("LTP", {NIFTY_EXCHANGE: [NIFTY_TOKEN]})
            price = _quote_price(response)
        except Exception as market_data_exc:
            try:
                response = getattr(smart_api, "ltpData")(NIFTY_EXCHANGE, NIFTY_SYMBOL, NIFTY_TOKEN)
                price = _quote_price(response)
            except Exception as legacy_exc:
                response_detail = _rejection_detail(response, secret_values)
                exception_detail = _safe_detail(
                    f"{type(legacy_exc).__name__}: {legacy_exc}", secret_values
                )
                detail = response_detail
                if "without an error description" in response_detail:
                    detail = exception_detail
                if not detail:
                    detail = _safe_detail(
                        f"{type(market_data_exc).__name__}: {market_data_exc}", secret_values
                    )
                message = f"NIFTY quote refresh failed · {detail}"
                with self._lock:
                    self._snapshot = replace(
                        self._snapshot,
                        angel_status="quote failed",
                        quote_error=detail,
                        last_message=message,
                    )
                raise ConnectionActionError(message) from legacy_exc
        if not isinstance(response, dict) or response.get("status") is False:
            detail = _rejection_detail(response, secret_values)
            message = f"NIFTY quote refresh failed · {detail}"
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    angel_status="quote failed",
                    quote_error=detail,
                    last_message=message,
                )
            raise ConnectionActionError(message)
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

    def refresh_intelligence_if_due(
        self, observed_at: datetime | None = None
    ) -> ConnectionSnapshot:
        """Refresh once per five-minute bucket while spot quotes update faster."""
        now = (observed_at or datetime.now(self._settings.timezone)).astimezone(
            self._settings.timezone
        )
        bucket = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        with self._lock:
            if bucket == self._last_intelligence_bucket:
                return self._snapshot
            self._last_intelligence_bucket = bucket
        return self.refresh_intelligence(now)

    def refresh_intelligence(self, observed_at: datetime | None = None) -> ConnectionSnapshot:
        """Load closed five-minute candles and calculate a read-only NIFTY signal."""
        with self._lock:
            smart_api = self._smart_api
        if smart_api is None:
            raise ConnectionActionError("Connect Angel One before loading market intelligence")
        now = (observed_at or datetime.now(self._settings.timezone)).astimezone(
            self._settings.timezone
        )
        credentials = load_credentials(self._settings.credentials_path)
        secrets = tuple(value.strip() for value in credentials.values() if value.strip())
        try:
            response = getattr(smart_api, "getCandleData")(
                {
                    "exchange": NIFTY_EXCHANGE,
                    "symboltoken": NIFTY_TOKEN,
                    "interval": NIFTY_CANDLE_INTERVAL,
                    "fromdate": (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M"),
                    "todate": now.strftime("%Y-%m-%d %H:%M"),
                }
            )
            if not isinstance(response, dict) or response.get("status") is False:
                raise ValueError(_rejection_detail(response, secrets))
            candles = _closed_five_minute_candles(
                response.get("data"), now=now, timezone=self._settings.timezone
            )
            if not candles:
                raise ValueError("Angel One returned no closed five-minute candles")
            snapshot = self._intelligence_snapshot(candles, now)
            saved = self.archive.save_candles(
                candles,
                token=NIFTY_TOKEN,
                exchange=NIFTY_EXCHANGE,
                timeframe=NIFTY_CANDLE_INTERVAL,
                collected_at=now,
            )
            with self._lock:
                spot = self._snapshot.nifty_price
            self.archive.save_observation(now, {**snapshot, "spot": spot})
            self.archive.record_run(now, "success", saved, 0, "NIFTY candles archived")
            self._refresh_archive_snapshot()
        except Exception as exc:
            detail = _safe_detail(f"{type(exc).__name__}: {exc}", secrets)
            message = f"NIFTY intelligence refresh failed · {detail}"
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    intelligence_status="failed",
                    intelligence_error=detail,
                    last_message=message,
                )
            raise ConnectionActionError(message) from exc
        with self._lock:
            self._last_intelligence_bucket = now.replace(
                minute=now.minute - now.minute % 5, second=0, microsecond=0
            )
            self._snapshot = replace(self._snapshot, **snapshot)
            result = self._snapshot
        self._send_signal_change_alert(result, credentials)
        return result

    def _intelligence_snapshot(
        self, candles: list[Candle], now: datetime
    ) -> dict[str, object]:
        closes = [candle.close for candle in candles]
        fast = ema(closes, 9)[-1]
        slow = ema(closes, 21)[-1]
        momentum = rsi(closes, 14)[-1]
        volatility = atr(
            [candle.high for candle in candles],
            [candle.low for candle in candles],
            closes,
            14,
        )
        latest = candles[-1].started_at
        in_session = now.weekday() < 5 and MARKET_OPEN <= now.time() < MARKET_CLOSE
        market_status = "OPEN" if in_session else "CLOSED"
        age = now - latest
        data_status = "fresh" if in_session and age <= timedelta(minutes=10) else "stale"
        if not in_session:
            data_status = "last closed session"

        strategy = MomentumStrategy()
        signal = strategy.evaluate(candles)
        if len(candles) < strategy.minimum_candles:
            label = "INSUFFICIENT DATA"
            confidence = None
            reason = f"Need {strategy.minimum_candles} closed candles; loaded {len(candles)}."
        elif in_session and data_status == "stale":
            label = "STALE DATA"
            confidence = None
            reason = "Latest closed candle is more than 10 minutes old during the session."
        elif not in_session:
            label = "MARKET CLOSED"
            confidence = None
            reason = "Outside the weekday 09:15–15:30 IST session window."
        elif signal is None:
            label = "NO TRADE"
            confidence = None
            reason = "EMA trend and RSI conditions do not currently align."
        else:
            label = signal.direction.value.upper()
            confidence = signal.confidence
            reason = signal.reason
        return {
            "intelligence_status": "ready",
            "intelligence_error": None,
            "market_status": market_status,
            "candle_count": len(candles),
            "latest_candle_at": latest,
            "data_status": data_status,
            "ema_fast": fast,
            "ema_slow": slow,
            "rsi_value": momentum,
            "atr_value": volatility,
            "signal_label": label,
            "signal_confidence": confidence,
            "signal_reason": reason,
            "last_message": "NIFTY five-minute intelligence refreshed; no order was placed",
        }

    def _send_signal_change_alert(
        self, snapshot: ConnectionSnapshot, credentials: dict[str, str]
    ) -> None:
        alertable = {"BULLISH", "BEARISH", "NO TRADE"}
        if snapshot.signal_label not in alertable:
            return
        with self._lock:
            if snapshot.signal_label == self._last_alerted_signal:
                return
        token = credentials.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = credentials.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            return
        try:
            self._notifier_factory(token, chat_id).send(
                "NIFTY 5-minute signal: "
                f"{snapshot.signal_label}. {snapshot.signal_reason} "
                "Read-only paper analysis; no order was placed."
            )
        except Exception:
            with self._lock:
                self._snapshot = replace(self._snapshot, telegram_status="signal alert failed")
            return
        with self._lock:
            self._last_alerted_signal = snapshot.signal_label
            self._snapshot = replace(self._snapshot, telegram_status="connected")

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
