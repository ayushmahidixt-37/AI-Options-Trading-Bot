from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from options_bot.config import Settings
from options_bot.connections import ConnectionManager
from options_bot.domain import Instrument, PaperOrderRequest, Quote
from options_bot.runner import build_application
from options_bot.web import create_web_app

IST = ZoneInfo("Asia/Kolkata")


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )


def auth() -> tuple[str, str]:
    return ("admin", "secret")


def test_web_dashboard_requires_password(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    response = client.get("/")

    assert response.status_code == 401


def test_web_lifespan_starts_and_stops_background_monitor(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    connections = ConnectionManager(cfg)
    events: list[str] = []
    connections.start_background_monitor = lambda: events.append("start")  # type: ignore[method-assign]
    connections.stop_background_monitor = lambda: events.append("stop")  # type: ignore[method-assign]

    with TestClient(create_web_app(cfg, "secret", connections)) as client:
        assert client.get("/", auth=auth()).status_code == 200
        assert events == ["start"]

    assert events == ["start", "stop"]


def test_web_dashboard_shows_paper_safety_and_actions(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    response = client.get("/", auth=auth())

    assert response.status_code == 200
    assert "Paper mode" in response.text
    assert "Run healthcheck" in response.text
    assert "Run paper scan" in response.text
    assert "No open paper positions" in response.text


def test_web_health_and_scan_actions_are_safe(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    health = client.post("/actions/healthcheck", auth=auth())
    scan = client.post("/actions/paper-scan", auth=auth())

    assert health.status_code == 200
    assert "Healthcheck passed" in health.text
    assert scan.status_code == 200
    assert "no order was placed" in scan.text


def test_web_connection_actions_show_nifty_and_telegram_status(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.env"
    credentials.write_text(
        "ANGEL_API_KEY=api\n"
        "ANGEL_CLIENT_CODE=CLIENT1234\n"
        "ANGEL_PASSWORD=pin\n"
        "ANGEL_TOTP_SECRET=secret\n"
        "TELEGRAM_BOT_TOKEN=token\n"
        "TELEGRAM_CHAT_ID=chat\n",
        encoding="utf-8",
    )
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials),
        }
    )

    class FakeApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def ltpData(self, *_args):
            return {"status": True, "data": {"ltp": 24600}}

        def getCandleData(self, _payload):
            now = datetime.now(IST)
            bucket = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
            rows = []
            close = 24500.0
            for index in range(60):
                open_price = close
                close += (1.0, 1.0, -1.0)[index % 3]
                started_at = bucket - timedelta(minutes=5 * (59 - index))
                rows.append(
                    [
                        started_at.isoformat(),
                        open_price,
                        max(open_price, close) + 2,
                        min(open_price, close) - 2,
                        close,
                        1000,
                    ]
                )
            return {"status": True, "data": rows}

    class FakeNotifier:
        def send(self, _text: str) -> None:
            return None

    connections = ConnectionManager(
        cfg,
        smart_api_factory=lambda _key: FakeApi(),
        totp_factory=lambda _secret: "123456",
        notifier_factory=lambda _token, _chat: FakeNotifier(),
    )
    client = TestClient(create_web_app(cfg, "secret", connections))

    assert client.post("/actions/angel-connect", auth=auth()).status_code == 200
    quote = client.post("/actions/nifty-refresh", auth=auth())
    intelligence = client.post("/actions/intelligence-refresh", auth=auth())
    telegram = client.post("/actions/telegram-test", auth=auth())

    assert "24600.00" in quote.text
    assert "NIFTY quote refreshed" in quote.text
    assert "15000" in quote.text
    assert "NIFTY five-minute market intelligence" in intelligence.text
    assert "EMA 9" in intelligence.text
    assert "BULLISH" in intelligence.text
    assert "no order was placed" in intelligence.text
    assert "Telegram test alert sent" in telegram.text
    csv_export = client.get("/archive/candles.csv", auth=auth())
    backup = client.post("/actions/archive-backup", auth=auth())
    assert csv_export.status_code == 200
    assert "instrument_token,symbol" in csv_export.text
    assert backup.status_code == 200
    assert backup.headers["content-type"] == "application/vnd.sqlite3"
    backtest = client.post("/actions/backtest", auth=auth())
    assert backtest.status_code == 200
    assert "Offline backtest" in backtest.text
    assert "INSUFFICIENT DATA" in backtest.text


def test_web_can_close_existing_paper_position(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    application = build_application(cfg)
    now = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
    instrument = Instrument("NIFTY_TEST_CE", "123", "NFO", "NIFTY", "CE", 50)
    order_id = application.paper_broker.buy(
        PaperOrderRequest(
            instrument=instrument,
            lots=1,
            quote=Quote(instrument.symbol, 100, now - timedelta(seconds=1)),
            stop_price=95,
            strategy="test",
            reason="fixture",
        ),
        now,
    )
    client = TestClient(create_web_app(cfg, "secret"))

    response = client.post(
        f"/positions/{order_id}/close",
        data={"exit_price": "102", "reason": "ui-test"},
        auth=auth(),
    )

    assert response.status_code == 200
    assert "Closed paper position" in response.text
    assert "No open paper positions" in response.text
