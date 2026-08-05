from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from options_bot.config import Settings
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
