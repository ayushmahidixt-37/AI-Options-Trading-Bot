from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from options_bot.config import Settings
from options_bot.connections import ConnectionManager, PaperTradeProposal
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


def test_revisited_post_action_redirects_to_dashboard(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    redirect = client.get(
        "/actions/healthcheck#paper",
        auth=auth(),
        follow_redirects=False,
    )

    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/#overview"
    recovered = client.get(redirect.headers["location"], auth=auth())
    assert recovered.status_code == 200
    assert "Today at a glance" in recovered.text


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
    assert "Automatic paper entries" in response.text
    assert "OFF" in response.text
    assert "Premium committed" in response.text
    assert "Capital used incl. entry fees" in response.text
    assert "Capital available" in response.text
    assert "Open this page refresh every 15 seconds" not in response.text
    assert "Open-position prices and this page refresh every 15 seconds" in response.text
    assert "window.setTimeout(refreshDashboard, 15000)" in response.text
    assert 'role="switch"' in response.text
    assert "location.reload()" not in response.text
    assert "location.replace(`/${location.hash}`)" in response.text

    rejected = client.post(
        "/actions/auto-paper",
        data={"enabled": "true", "confirmation": "wrong"},
        auth=auth(),
    )
    assert "Type ENABLE AUTO PAPER exactly" in rejected.text
    enabled = client.post(
        "/actions/auto-paper",
        data={"enabled": "true", "confirmation": "ENABLE AUTO PAPER"},
        auth=auth(),
    )
    assert "Automatic paper entries enabled" in enabled.text
    assert "monitoring started immediately" in enabled.text
    assert "RUNNING" in enabled.text
    assert "Research workspace" in enabled.text
    assert "Data &amp; operations" in enabled.text
    assert "Readiness review" in enabled.text


def test_strategy_validation_form_validates_ranges_and_exports(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))
    invalid = client.post(
        "/actions/strategy-validation",
        data={
            "development_start": "2026-08-01",
            "development_end": "2026-08-10",
            "validation_start": "2026-08-10",
            "validation_end": "2026-08-15",
            "test_start": "2026-08-16",
            "test_end": "2026-08-20",
        },
        auth=auth(),
    )
    assert "non-overlapping chronological" in invalid.text
    valid = client.post(
        "/actions/strategy-validation",
        data={
            "development_start": "2026-08-01",
            "development_end": "2026-08-05",
            "validation_start": "2026-08-06",
            "validation_end": "2026-08-10",
            "test_start": "2026-08-11",
            "test_end": "2026-08-15",
        },
        auth=auth(),
    )
    assert "Strategy validation completed" in valid.text
    assert client.get("/validation/comparison.csv", auth=auth()).status_code == 200


def test_readiness_review_is_persistent_and_never_approves_live(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))
    rejected = client.post(
        "/actions/readiness-review",
        data={"confirmation": "wrong"},
        auth=auth(),
    )
    assert "Type SAVE PAPER REVIEW exactly" in rejected.text

    saved = client.post(
        "/actions/readiness-review",
        data={
            "confirmation": "SAVE PAPER REVIEW",
            "broker_restrictions": "true",
            "recovery_drill": "true",
            "user_acceptance": "true",
        },
        auth=auth(),
    )
    assert "Paper-readiness acknowledgements saved" in saved.text
    assert "NOT APPROVED" in saved.text
    report = client.get("/readiness/report.csv", auth=auth())
    assert report.status_code == 200
    assert "live_trading_approved,false" in report.text


def test_web_health_and_scan_actions_are_safe(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    health = client.post("/actions/healthcheck", auth=auth())
    scan = client.post("/actions/paper-scan", auth=auth())

    assert health.status_code == 200
    assert "Healthcheck passed" in health.text
    assert scan.status_code == 200
    assert "no order was placed" in scan.text
    integrity = client.post("/actions/archive-verify", auth=auth())
    assert "Archive integrity: ok" in integrity.text
    invalid_dates = client.post(
        "/actions/backtest",
        data={"start_date": "2026-08-10", "end_date": "2026-08-01"},
        auth=auth(),
    )
    assert "Start date must not be after end date" in invalid_dates.text


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
    assert "Read-only signal" in intelligence.text
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


def test_web_requires_separate_confirmation_for_paper_proposal(tmp_path: Path) -> None:
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "ENTRY_START_IST": "00:00",
            "ENTRY_CUTOFF_IST": "23:58",
            "FORCE_EXIT_IST": "23:59",
        }
    )
    manager = ConnectionManager(cfg)
    now = datetime.now(IST)
    instrument = Instrument(
        "NIFTY_TEST_CE",
        "123",
        "NFO",
        "NIFTY",
        "CE",
        75,
        now.date() + timedelta(days=7),
        24600,
    )
    proposal = PaperTradeProposal(
        "proposal-1",
        instrument,
        Quote(instrument.symbol, 100, now),
        96,
        "BULLISH",
        0.57,
        "fixture",
        358.75,
    )
    manager.create_paper_proposal = lambda: proposal  # type: ignore[method-assign]
    client = TestClient(create_web_app(cfg, "secret", manager))

    displayed = client.post("/actions/paper-proposal", auth=auth())
    assert "no order was placed" in displayed.text
    assert "Confirm one-lot paper entry" in displayed.text

    confirmed = client.post(
        "/actions/paper-confirm",
        data={"proposal_id": proposal.proposal_id},
        auth=auth(),
    )
    assert "Opened paper position" in confirmed.text
    assert "NIFTY_TEST_CE" in confirmed.text
