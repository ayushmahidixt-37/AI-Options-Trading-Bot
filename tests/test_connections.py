from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.connections import (
    ConnectionActionError,
    ConnectionManager,
    _closed_five_minute_candles,
)

IST = ZoneInfo("Asia/Kolkata")


def test_smartapi_market_data_client_imports_with_runtime_dependencies() -> None:
    from SmartApi.smartConnect import SmartConnect

    assert SmartConnect.__name__ == "SmartConnect"


def settings(tmp_path: Path, credentials_path: Path) -> Settings:
    return Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials_path),
        }
    )


def credential_file(tmp_path: Path) -> Path:
    path = tmp_path / "credentials.env"
    path.write_text(
        "\n".join(
            (
                "ANGEL_API_KEY=api-key",
                "ANGEL_CLIENT_CODE=CLIENT1234",
                "ANGEL_PASSWORD=pin",
                "ANGEL_TOTP_SECRET=totp-secret",
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "TELEGRAM_CHAT_ID=chat-id",
            )
        ),
        encoding="utf-8",
    )
    return path


def candle_rows(now: datetime, count: int = 60) -> list[list[object]]:
    current_bucket = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
    start = current_bucket - timedelta(minutes=5 * (count - 1))
    close = 24500.0
    rows: list[list[object]] = []
    changes = (1.0, 1.0, -1.0)
    for index in range(count):
        open_price = close
        close += changes[index % len(changes)]
        rows.append(
            [
                (start + timedelta(minutes=5 * index)).isoformat(),
                open_price,
                max(open_price, close) + 2,
                min(open_price, close) - 2,
                close,
                1000,
            ]
        )
    return rows


def test_connects_and_refreshes_nifty_without_order_methods(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)

    class FakeApi:
        def generateSession(self, client_code: str, password: str, totp: str) -> dict[str, object]:
            assert (client_code, password, totp) == ("CLIENT1234", "pin", "123456")
            return {"status": True, "data": {"jwtToken": "never-exposed"}}

        def getMarketData(self, mode: str, tokens: dict[str, list[str]]) -> dict[str, object]:
            assert (mode, tokens) == ("LTP", {"NSE": ["99926000"]})
            return {"status": True, "data": {"fetched": [{"ltp": 24567.8}]}}

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda api_key: FakeApi() if api_key == "api-key" else None,
        totp_factory=lambda secret: "123456" if secret == "totp-secret" else "",
    )

    connected = manager.connect_angel()
    refreshed = manager.refresh_nifty()

    assert connected.angel_status == "connected"
    assert connected.angel_client.endswith("1234")
    assert "CLIENT" not in connected.angel_client
    assert refreshed.nifty_price == 24567.8
    assert refreshed.quote_observed_at is not None


def test_incomplete_angel_credentials_fail_without_factory_call(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.env"
    credentials.write_text("ANGEL_API_KEY=\n", encoding="utf-8")
    called = False

    def factory(_api_key: str) -> object:
        nonlocal called
        called = True
        return object()

    manager = ConnectionManager(settings(tmp_path, credentials), smart_api_factory=factory)

    with pytest.raises(ConnectionActionError, match="incomplete"):
        manager.connect_angel()

    assert called is False


def test_rejected_login_shows_safe_angel_error_code_and_message(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)

    class RejectingApi:
        def generateSession(self, *_args):
            return {
                "status": False,
                "errorcode": "AB1234",
                "message": "Invalid credentials for CLIENT1234 using api-key",
            }

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _api_key: RejectingApi(),
        totp_factory=lambda _secret: "123456",
    )

    with pytest.raises(ConnectionActionError, match="AB1234") as error:
        manager.connect_angel()

    snapshot = manager.snapshot()
    assert snapshot.angel_error == "AB1234 · Invalid credentials for [redacted] using [redacted]"
    assert "CLIENT1234" not in str(error.value)
    assert "api-key" not in str(error.value)


def test_angel_camel_case_error_code_is_preserved() -> None:
    from options_bot.connections import _rejection_detail

    response = {"success": False, "message": "Invalid API Key", "errorCode": "AG8004"}

    assert _rejection_detail(response, ()) == "AG8004 · Invalid API Key"


def test_rejected_nifty_quote_shows_safe_error_code_and_message(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)

    class QuoteRejectingApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def getMarketData(self, *_args):
            return {
                "status": False,
                "errorcode": "AB5678",
                "message": "Invalid symbol for api-key",
                "data": None,
            }

        def ltpData(self, *_args):
            return {
                "status": False,
                "errorcode": "AB5678",
                "message": "Invalid symbol for api-key",
                "data": None,
            }

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _api_key: QuoteRejectingApi(),
        totp_factory=lambda _secret: "123456",
    )
    manager.connect_angel()

    with pytest.raises(ConnectionActionError, match="AB5678") as error:
        manager.refresh_nifty()

    snapshot = manager.snapshot()
    assert snapshot.quote_error == "AB5678 · Invalid symbol for [redacted]"
    assert "api-key" not in str(error.value)


def test_nifty_quote_falls_back_to_legacy_ltp_method(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)

    class LegacyApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def getMarketData(self, *_args):
            raise RuntimeError("new quote API unavailable")

        def ltpData(self, exchange, symbol, token):
            assert (exchange, symbol, token) == ("NSE", "Nifty 50", "99926000")
            return {"status": True, "data": {"ltp": 24570}}

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _api_key: LegacyApi(),
        totp_factory=lambda _secret: "123456",
    )
    manager.connect_angel()

    assert manager.refresh_nifty().nifty_price == 24570


def test_refreshes_closed_five_minute_intelligence_and_suppresses_duplicate_alerts(
    tmp_path: Path,
) -> None:
    credentials = credential_file(tmp_path)
    now = datetime(2026, 8, 6, 13, 53, tzinfo=IST)
    messages: list[str] = []
    candle_requests = 0

    class FakeApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def getCandleData(self, payload):
            nonlocal candle_requests
            candle_requests += 1
            assert payload["interval"] == "FIVE_MINUTE"
            assert payload["symboltoken"] == "99926000"
            return {"status": True, "data": candle_rows(now)}

    class FakeNotifier:
        def send(self, text: str) -> None:
            messages.append(text)

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _api_key: FakeApi(),
        totp_factory=lambda _secret: "123456",
        notifier_factory=lambda _token, _chat: FakeNotifier(),
    )
    manager.connect_angel()

    first = manager.refresh_intelligence_if_due(now)
    second = manager.refresh_intelligence_if_due(now)

    assert first.intelligence_status == "ready"
    assert first.market_status == "OPEN"
    assert first.data_status == "fresh"
    assert first.candle_count == 59
    assert first.latest_candle_at == datetime(2026, 8, 6, 13, 45, tzinfo=IST)
    assert first.signal_label == "BULLISH"
    assert first.ema_fast is not None
    assert first.ema_slow is not None
    assert first.rsi_value is not None
    assert first.atr_value is not None
    assert second.signal_label == first.signal_label
    assert candle_requests == 1
    assert len(messages) == 1
    assert "no order was placed" in messages[0]


def test_background_cycle_connects_and_refreshes_read_only_data(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)
    now = datetime(2026, 8, 6, 13, 53, tzinfo=IST)

    class FakeApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def getMarketData(self, *_args):
            return {"status": True, "data": {"fetched": [{"ltp": 24629.4}]}}

        def getCandleData(self, _payload):
            return {"status": True, "data": candle_rows(now)}

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _api_key: FakeApi(),
        totp_factory=lambda _secret: "123456",
        instrument_master_loader=lambda: [],
        notifier_factory=lambda _token, _chat: type(
            "Notifier", (), {"send": lambda self, _text: None}
        )(),
    )

    snapshot = manager.run_background_cycle(now)

    assert snapshot.monitor_status == "running"
    assert snapshot.monitor_last_run_at == now
    assert snapshot.monitor_error is None
    assert snapshot.nifty_price == 24629.4
    assert snapshot.intelligence_status == "ready"


def test_archives_current_nifty_option_instruments(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)
    rows = [
        {
            "exch_seg": "NFO",
            "instrumenttype": "OPTIDX",
            "symbol": "NIFTY13AUG2624600CE",
            "token": "123",
            "name": "NIFTY",
            "strike": "2460000",
            "expiry": "13AUG2026",
            "lotsize": "75",
        },
        {
            "exch_seg": "NFO",
            "instrumenttype": "OPTIDX",
            "symbol": "BANKNIFTY13AUG2650000CE",
            "token": "456",
            "name": "BANKNIFTY",
            "strike": "5000000",
            "expiry": "13AUG2026",
            "lotsize": "30",
        },
    ]
    manager = ConnectionManager(
        settings(tmp_path, credentials), instrument_master_loader=lambda: rows
    )

    snapshot = manager.refresh_instrument_archive(
        datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    )

    assert snapshot.archive_status == "ready"
    assert snapshot.archived_instruments == 1
    assert snapshot.nearest_expiry == "2026-08-13"


def test_collects_bounded_nearest_expiry_option_candles(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)
    now = datetime(2026, 8, 6, 13, 53, tzinfo=IST)
    requests: list[str] = []

    class FakeApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def getCandleData(self, payload):
            requests.append(payload["symboltoken"])
            return {"status": True, "data": candle_rows(now, 3)}

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _key: FakeApi(),
        totp_factory=lambda _secret: "123456",
        instrument_master_loader=lambda: [],
        request_pause=lambda _seconds: None,
    )
    manager.connect_angel()
    manager._snapshot = replace(manager.snapshot(), nifty_price=24620)
    manager.archive.save_instruments(
        [
            Instrument(
                f"NIFTY13AUG2624600{kind}",
                kind,
                "NFO",
                "NIFTY",
                kind,
                75,
                date(2026, 8, 13),
                24600,
            )
            for kind in ("CE", "PE")
        ],
        now,
    )

    snapshot = manager.refresh_option_archive(now)

    assert requests == ["CE", "PE"]
    assert snapshot.option_archive_status == "success"
    assert snapshot.option_contracts_tracked == 2
    assert snapshot.archived_option_candles == 4


def test_creates_read_only_atm_paper_proposal_with_bounded_risk(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)
    now = datetime(2026, 8, 6, 13, 53, tzinfo=IST)

    class FakeApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def getMarketData(self, *_args):
            return {"status": True, "data": {"fetched": [{"ltp": 100.0}]}}

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _key: FakeApi(),
        totp_factory=lambda _secret: "123456",
        instrument_master_loader=lambda: [],
    )
    manager.connect_angel()
    manager.archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2624600CE",
                "CE",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                24600,
            )
        ],
        now,
    )
    manager._snapshot = replace(
        manager.snapshot(),
        nifty_price=24620,
        signal_label="BULLISH",
        signal_confidence=0.57,
        signal_reason="fixture",
        data_status="fresh",
    )

    proposal = manager.create_paper_proposal(now)

    assert proposal.instrument.option_type == "CE"
    assert proposal.quote.price == 100
    assert 0 < proposal.stop_price < proposal.quote.price
    assert proposal.estimated_max_loss < manager._settings.max_loss_per_trade


def test_candle_parser_rejects_duplicates_and_excludes_forming_candle() -> None:
    now = datetime(2026, 8, 6, 13, 53, tzinfo=IST)
    rows = candle_rows(now, 3)

    parsed = _closed_five_minute_candles(rows, now=now, timezone=IST)
    assert len(parsed) == 2
    assert parsed[-1].started_at == datetime(2026, 8, 6, 13, 45, tzinfo=IST)

    rows[1][0] = rows[0][0]
    with pytest.raises(ValueError, match="duplicate or out-of-order"):
        _closed_five_minute_candles(rows, now=now, timezone=IST)


def test_intelligence_fails_closed_for_insufficient_and_stale_data(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)
    manager = ConnectionManager(settings(tmp_path, credentials))
    now = datetime(2026, 8, 6, 13, 53, tzinfo=IST)
    short = _closed_five_minute_candles(candle_rows(now, 20), now=now, timezone=IST)

    insufficient = manager._intelligence_snapshot(short, now)
    assert insufficient["signal_label"] == "INSUFFICIENT DATA"

    stale_rows = candle_rows(now - timedelta(minutes=20), 60)
    stale = _closed_five_minute_candles(stale_rows, now=now, timezone=IST)
    stale_snapshot = manager._intelligence_snapshot(stale, now)
    assert stale_snapshot["data_status"] == "stale"
    assert stale_snapshot["signal_label"] == "STALE DATA"


def test_sends_alert_only_telegram_message(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)
    messages: list[str] = []

    class FakeNotifier:
        def send(self, text: str) -> None:
            messages.append(text)

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        notifier_factory=lambda token, chat_id: FakeNotifier()
        if (token, chat_id) == ("telegram-token", "chat-id")
        else None,
    )

    result = manager.test_telegram()

    assert result.telegram_status == "connected"
    assert messages == [
        "AI Options Trading Bot: Telegram alerts connected. Paper mode only; no trade was placed."
    ]
