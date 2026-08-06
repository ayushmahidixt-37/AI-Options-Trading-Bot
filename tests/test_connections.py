from __future__ import annotations

from pathlib import Path

import pytest

from options_bot.config import Settings
from options_bot.connections import ConnectionActionError, ConnectionManager


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


def test_connects_and_refreshes_nifty_without_order_methods(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)

    class FakeApi:
        def generateSession(self, client_code: str, password: str, totp: str) -> dict[str, object]:
            assert (client_code, password, totp) == ("CLIENT1234", "pin", "123456")
            return {"status": True, "data": {"jwtToken": "never-exposed"}}

        def ltpData(self, exchange: str, symbol: str, token: str) -> dict[str, object]:
            assert (exchange, symbol, token) == ("NSE", "Nifty 50", "99926000")
            return {"status": True, "data": {"ltp": 24567.8}}

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


def test_rejected_nifty_quote_shows_safe_error_code_and_message(tmp_path: Path) -> None:
    credentials = credential_file(tmp_path)

    class QuoteRejectingApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

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
