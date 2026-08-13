from __future__ import annotations

from datetime import date, datetime, timedelta
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


def test_upstox_tab_is_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    ingest = client.post(
        "/actions/upstox-ingest",
        data={"start_date": "2026-08-01", "end_date": "2026-08-07"},
        auth=auth(),
    )
    backtest = client.post("/actions/upstox-backtest", auth=auth())

    assert "UPSTOX_BACKTEST_ENABLED=true" in ingest.text
    assert "UPSTOX_BACKTEST_ENABLED=true" in backtest.text
    assert client.get("/upstox/trades.csv", auth=auth()).status_code == 404


def test_upstox_ingest_requires_credentials_when_enabled(tmp_path: Path) -> None:
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    client = TestClient(create_web_app(cfg, "secret"))

    ingest = client.post(
        "/actions/upstox-ingest",
        data={"start_date": "2026-08-01", "end_date": "2026-08-07"},
        auth=auth(),
    )

    assert "Upstox credentials are incomplete" in ingest.text or "Credential file not found" in ingest.text


def test_upstox_ingest_rejects_backwards_date_range_when_enabled(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.env"
    credentials.write_text("UPSTOX_ACCESS_TOKEN=test-token\n", encoding="utf-8")
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    client = TestClient(create_web_app(cfg, "secret"))

    ingest = client.post(
        "/actions/upstox-ingest",
        data={"start_date": "2026-08-10", "end_date": "2026-08-01"},
        auth=auth(),
    )

    assert "Start date must not be after end date" in ingest.text


def test_upstox_ingest_happy_path_uses_pull_range(tmp_path: Path, monkeypatch) -> None:
    import options_bot.web as web_module
    from options_bot.upstox_ingest import IngestionSummary

    credentials = tmp_path / "credentials.env"
    credentials.write_text("UPSTOX_ACCESS_TOKEN=test-token\n", encoding="utf-8")
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    fake_summary = IngestionSummary(
        contracts_planned=2, contracts_pulled=2, candles_saved=10, instruments_saved=2, warnings=()
    )
    monkeypatch.setattr(
        web_module,
        "pull_range",
        lambda client, archive, start, end, **kwargs: fake_summary,
    )
    client = TestClient(create_web_app(cfg, "secret"))

    ingest = client.post(
        "/actions/upstox-ingest",
        data={"start_date": "2026-08-01", "end_date": "2026-08-07"},
        auth=auth(),
    )

    assert "Upstox ingestion complete: 10 candles, 2 contracts" in ingest.text


def test_upstox_ingest_message_reports_skipped_cached_chunks(tmp_path: Path, monkeypatch) -> None:
    import options_bot.web as web_module
    from options_bot.upstox_ingest import IngestionSummary

    credentials = tmp_path / "credentials.env"
    credentials.write_text("UPSTOX_ACCESS_TOKEN=test-token\n", encoding="utf-8")
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    fake_summary = IngestionSummary(
        contracts_planned=2,
        contracts_pulled=2,
        candles_saved=0,
        instruments_saved=2,
        warnings=(),
        chunks_skipped_cached=3,
    )
    monkeypatch.setattr(
        web_module, "pull_range", lambda client, archive, start, end, **kwargs: fake_summary
    )
    client = TestClient(create_web_app(cfg, "secret"))

    ingest = client.post(
        "/actions/upstox-ingest",
        data={"start_date": "2026-08-01", "end_date": "2026-08-07"},
        auth=auth(),
    )

    assert "3 chunk(s) already cached, skipped" in ingest.text


def test_upstox_ingest_passes_force_refetch_through_to_pull_range(tmp_path: Path, monkeypatch) -> None:
    import options_bot.web as web_module
    from options_bot.upstox_ingest import IngestionSummary

    credentials = tmp_path / "credentials.env"
    credentials.write_text("UPSTOX_ACCESS_TOKEN=test-token\n", encoding="utf-8")
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    captured: dict[str, object] = {}

    def fake_pull_range(client, archive, start, end, **kwargs):
        captured.update(kwargs)
        return IngestionSummary(0, 0, 0, 0, ())

    monkeypatch.setattr(web_module, "pull_range", fake_pull_range)
    client = TestClient(create_web_app(cfg, "secret"))

    client.post(
        "/actions/upstox-ingest",
        data={"start_date": "2026-08-01", "end_date": "2026-08-07", "force_refetch": "true"},
        auth=auth(),
    )

    assert captured["force_refetch"] is True


def test_upstox_backtest_reports_insufficient_data_and_csv_is_404_until_run(tmp_path: Path) -> None:
    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    client = TestClient(create_web_app(cfg, "secret"))

    assert client.get("/upstox/trades.csv", auth=auth()).status_code == 404

    backtest = client.post("/actions/upstox-backtest", auth=auth())

    assert "Historical backtest (Upstox)" in backtest.text
    assert "INSUFFICIENT DATA" in backtest.text
    assert client.get("/upstox/trades.csv", auth=auth()).status_code == 404


def test_upstox_backtest_records_range_usage_for_the_ledger(tmp_path: Path) -> None:
    from options_bot.upstox_data import UpstoxCandle
    from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    connections = ConnectionManager(cfg)
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    candles = [
        UpstoxCandle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(4)
    ]
    connections.archive.save_upstox_candles(
        candles, token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE", collected_at=start,
    )
    client = TestClient(create_web_app(cfg, "secret", connections))

    client.post("/actions/upstox-backtest", auth=auth())

    with connections.archive.connect() as con:
        rows = con.execute(
            "SELECT candidate_name, role, range_start, range_end FROM range_usage "
            "WHERE candidate_name='dashboard-backtest'"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][1] == "screening"
    assert rows[0][2] == "2026-08-03"
    assert rows[0][3] == "2026-08-03"

    # A later CLI test attempt over the exact same date must now be blocked,
    # proving the dashboard-touched range is visible to the ledger.
    from options_bot.research_ledger import UsageRole, check_range

    blocked = check_range(
        connections.archive, candidate_name="Any candidate", role=UsageRole.TEST,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 8, 3), end=date(2026, 8, 3),
    )
    assert blocked.allowed is False


def test_upstox_analysis_summary_is_404_until_backtest_then_returns_plain_text(
    tmp_path: Path, monkeypatch
) -> None:
    import options_bot.web as web_module
    from options_bot.backtest import BacktestResult
    from options_bot.upstox_analysis import DeepAnalysisReport

    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    fake_result = BacktestResult("VALIDATION READY", 1, 1, 0, 100.0, 1.0, 100.0, 0.0, 100.0, None, "ok")
    fake_report = DeepAnalysisReport(
        overall=fake_result,
        time_of_day=(),
        day_of_week=(),
        expiry_day=(),
        volatility_regime=(),
        variants=(),
    )
    monkeypatch.setattr(web_module, "run_deep_analysis", lambda archive, **kwargs: fake_report)
    client = TestClient(create_web_app(cfg, "secret"))

    assert client.get("/upstox/analysis-summary.txt", auth=auth()).status_code == 404

    client.post("/actions/upstox-backtest", auth=auth())
    summary = client.get("/upstox/analysis-summary.txt", auth=auth())

    assert summary.status_code == 200
    assert summary.headers["content-type"].startswith("text/plain")
    assert "UPSTOX BACKTEST ANALYSIS SUMMARY" in summary.text
    assert "Status: VALIDATION READY" in summary.text


def test_upstox_custom_backtest_is_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    result = client.post("/actions/upstox-custom-backtest", auth=auth())

    assert "UPSTOX_BACKTEST_ENABLED=true" in result.text
    assert client.get("/upstox/custom-trades.csv", auth=auth()).status_code == 404
    assert client.get("/upstox/custom-analysis-summary.txt", auth=auth()).status_code == 404


def test_upstox_custom_backtest_parses_form_fields_into_parameters(
    tmp_path: Path, monkeypatch
) -> None:
    import options_bot.web as web_module
    from options_bot.backtest import BacktestParameters, BacktestResult, OptionBacktestTrade
    from options_bot.upstox_analysis import DeepAnalysisReport

    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    fake_trade = OptionBacktestTrade(
        signal_at=datetime(2026, 7, 1, 10, 0, tzinfo=IST),
        direction="BULLISH",
        token="NSE_FO|1|31-12-2026",
        symbol="NIFTY CE",
        entry_at=datetime(2026, 7, 1, 10, 5, tzinfo=IST),
        entry_price=100.0,
        stop_price=90.0,
        exit_at=datetime(2026, 7, 1, 10, 10, tzinfo=IST),
        exit_price=101.0,
        exit_reason="force-exit",
        units=75,
        gross_pnl=75.0,
        fees=40.0,
        net_pnl=35.0,
        raw_points=1.0,
    )
    fake_result = BacktestResult(
        "PRELIMINARY", 3, 1, 2, 10.0, 0.33, 100.0, 0.0, 50.0, None, "ok", (fake_trade,)
    )
    fake_report = DeepAnalysisReport(
        overall=fake_result,
        time_of_day=(),
        day_of_week=(),
        expiry_day=(),
        volatility_regime=(),
        variants=(),
    )
    captured: dict[str, object] = {}

    def fake_run_deep_analysis(archive, **kwargs):
        captured.update(kwargs)
        return fake_report

    monkeypatch.setattr(web_module, "run_deep_analysis", fake_run_deep_analysis)
    client = TestClient(create_web_app(cfg, "secret"))

    result = client.post(
        "/actions/upstox-custom-backtest",
        data={
            "start_date": "2026-07-01",
            "end_date": "2026-07-28",
            "maximum_hold_minutes": "30",
            "target_return_pct": "20",
            "trailing_stop_pct": "10",
            "no_stop_cap": "true",
            "exclude_expiry_day": "true",
            "entry_start": "09:30",
            "entry_end": "12:00",
            "weekday_tuesday": "true",
            "weekday_thursday": "true",
        },
        auth=auth(),
    )

    assert "Custom Upstox backtest completed" in result.text
    parameters = captured["variants"][0]
    assert isinstance(parameters, BacktestParameters)
    assert parameters.stop_risk_fraction is None
    assert parameters.maximum_hold_minutes == 30
    assert parameters.target_return == 0.2
    assert parameters.trailing_stop == 0.1
    assert parameters.exclude_expiry_day is True
    assert parameters.entry_start.strftime("%H:%M") == "09:30"
    assert parameters.entry_end.strftime("%H:%M") == "12:00"
    assert parameters.allowed_weekdays == (1, 3)

    assert client.get("/upstox/custom-trades.csv", auth=auth()).status_code == 200
    summary = client.get("/upstox/custom-analysis-summary.txt", auth=auth())
    assert summary.status_code == 200
    assert "UPSTOX BACKTEST ANALYSIS SUMMARY" in summary.text


def test_upstox_validation_is_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(create_web_app(settings(tmp_path), "secret"))

    result = client.post(
        "/actions/upstox-validation",
        data={
            "development_start": "2026-06-01",
            "development_end": "2026-06-10",
            "validation_start": "2026-06-11",
            "validation_end": "2026-06-20",
            "test_start": "2026-06-21",
            "test_end": "2026-06-30",
        },
        auth=auth(),
    )

    assert "UPSTOX_BACKTEST_ENABLED=true" in result.text
    assert client.get("/upstox/validation.csv", auth=auth()).status_code == 404


def test_upstox_validation_uses_run_upstox_backtest_as_the_runner(
    tmp_path: Path, monkeypatch
) -> None:
    import options_bot.web as web_module
    from options_bot.upstox_backtest import run_upstox_backtest
    from options_bot.validation import ValidationReport

    cfg = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "UPSTOX_BACKTEST_ENABLED": "true",
        }
    )
    fake_report = ValidationReport(
        "PRELIMINARY",
        "Baseline",
        (),
        "warning",
        (date(2026, 6, 1), date(2026, 6, 10)),
        (date(2026, 6, 11), date(2026, 6, 20)),
        (date(2026, 6, 21), date(2026, 6, 30)),
    )
    captured: dict[str, object] = {}

    def fake_run_strategy_validation(archive, settings, **kwargs):
        captured.update(kwargs)
        return fake_report

    monkeypatch.setattr(web_module, "run_strategy_validation", fake_run_strategy_validation)
    client = TestClient(create_web_app(cfg, "secret"))

    assert client.get("/upstox/validation.csv", auth=auth()).status_code == 404

    result = client.post(
        "/actions/upstox-validation",
        data={
            "development_start": "2026-06-01",
            "development_end": "2026-06-10",
            "validation_start": "2026-06-11",
            "validation_end": "2026-06-20",
            "test_start": "2026-06-21",
            "test_end": "2026-06-30",
        },
        auth=auth(),
    )

    assert "Upstox strategy validation completed" in result.text
    assert captured["runner"] is run_upstox_backtest
    assert client.get("/upstox/validation.csv", auth=auth()).status_code == 200
