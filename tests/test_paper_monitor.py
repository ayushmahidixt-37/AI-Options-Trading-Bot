from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.config import Settings
from options_bot.connections import ConnectionManager, PaperTradeProposal
from options_bot.domain import Instrument, PaperOrderRequest, Quote
from options_bot.paper_monitor import PaperPositionMonitor
from options_bot.runner import build_application

IST = ZoneInfo("Asia/Kolkata")


def test_monitor_closes_confirmed_paper_position_at_stop(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        }
    )
    application = build_application(settings)
    connections = ConnectionManager(settings)
    now = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    instrument = Instrument("NIFTY_TEST_CE", "123", "NFO", "NIFTY", "CE", 75)
    order_id = application.paper_broker.buy(
        PaperOrderRequest(
            instrument=instrument,
            lots=1,
            quote=Quote(instrument.symbol, 100, now),
            stop_price=96,
            strategy="test",
            reason="fixture",
        ),
        now,
    )
    connections._snapshot = replace(connections.snapshot(), signal_label="BULLISH")
    connections.quote_instrument = lambda _instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 95, observed_at or now
    )
    monitor = PaperPositionMonitor(application, connections)

    snapshot = monitor.run_cycle(now)

    assert snapshot.status == "running"
    assert snapshot.positions_checked == 1
    assert snapshot.positions_closed == 1
    assert snapshot.recovered_positions == 1
    assert "paper-stop" in snapshot.last_action
    assert application.ledger.open_positions() == []
    with application.ledger.connect() as con:
        row = con.execute(
            "SELECT status, close_reason FROM paper_orders WHERE id=?", (order_id,)
        ).fetchone()
    assert tuple(row) == ("CLOSED", "paper-stop")


def test_monitor_keeps_position_when_no_exit_rule_matches(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        }
    )
    application = build_application(settings)
    connections = ConnectionManager(settings)
    now = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    instrument = Instrument("NIFTY_TEST_CE", "123", "NFO", "NIFTY", "CE", 75)
    application.paper_broker.buy(
        PaperOrderRequest(
            instrument=instrument,
            lots=1,
            quote=Quote(instrument.symbol, 100, now),
            stop_price=96,
            strategy="test",
            reason="fixture",
        ),
        now,
    )
    connections._snapshot = replace(connections.snapshot(), signal_label="BULLISH")
    connections.quote_instrument = lambda _instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 102, observed_at or now
    )

    snapshot = PaperPositionMonitor(application, connections).run_cycle(now)

    assert snapshot.positions_closed == 0
    assert snapshot.recovered_positions == 1
    assert snapshot.total_unrealized_pnl == 72.5
    assert snapshot.position_quotes[0]["ltp"] == 102
    assert len(application.ledger.open_positions()) == 1


def test_monitor_kill_switch_requires_confirmation_and_closes_all(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        }
    )
    application = build_application(settings)
    connections = ConnectionManager(settings)
    now = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    instrument = Instrument("NIFTY_TEST_CE", "123", "NFO", "NIFTY", "CE", 75)
    application.paper_broker.buy(
        PaperOrderRequest(
            instrument=instrument,
            lots=1,
            quote=Quote(instrument.symbol, 100, now),
            stop_price=96,
            strategy="test",
            reason="fixture",
        ),
        now,
    )
    connections.quote_instrument = lambda _instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 101, observed_at or now
    )
    monitor = PaperPositionMonitor(application, connections)

    try:
        monitor.close_all("wrong", now)
    except ValueError as exc:
        assert str(exc) == "Type CLOSE ALL PAPER exactly"
    else:
        raise AssertionError("kill switch accepted an incorrect confirmation")

    snapshot = monitor.close_all("CLOSE ALL PAPER", now)
    assert snapshot.positions_closed == 1
    assert application.ledger.open_positions() == []
    assert application.ledger.daily_summary("2026-08-06")["trades"] == 1
    assert application.ledger.recent_events()[0]["event_type"] == "paper_kill_switch"


def test_automatic_paper_entry_is_confirmed_persistent_and_duplicate_safe(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        }
    )
    application = build_application(settings)
    connections = ConnectionManager(settings)
    now = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    instrument = Instrument(
        "NIFTY_TEST_CE", "123", "NFO", "NIFTY", "CE", 75, now.date(), 24600
    )
    proposal = PaperTradeProposal(
        "auto-1",
        instrument,
        Quote(instrument.symbol, 100, now),
        96,
        "BULLISH",
        0.6,
        "fixture",
        340,
        now,
        24600,
        24610,
        24590,
        58,
        20,
    )
    connections._snapshot = replace(
        connections.snapshot(),
        signal_label="BULLISH",
        latest_candle_at=now,
        data_status="fresh",
    )
    connections.create_paper_proposal = lambda _now=None: proposal  # type: ignore[method-assign]
    monitor = PaperPositionMonitor(application, connections)

    try:
        monitor.set_auto_entry(True, "wrong")
    except ValueError:
        pass
    else:
        raise AssertionError("automatic paper entry accepted incorrect confirmation")
    monitor.set_auto_entry(True, "ENABLE AUTO PAPER")
    first = monitor.run_cycle(now)
    second = monitor.run_cycle(now)

    assert first.auto_entry_enabled is True
    assert len(application.ledger.open_positions()) == 1
    assert second.auto_entry_last_action == first.auto_entry_last_action
    assert application.ledger.get_state("auto_paper_last_signal") == now.isoformat()
    journal = application.ledger.trade_journal()
    assert journal[0]["direction"] == "BULLISH"
    assert journal[0]["rsi_value"] == 58
    restarted = PaperPositionMonitor(application, connections)
    assert restarted.snapshot().auto_entry_enabled is True
    assert restarted.set_auto_entry(False, "DISABLE AUTO PAPER").auto_entry_enabled is False


def test_daily_report_is_sent_once_and_survives_restart(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        }
    )
    application = build_application(settings)
    connections = ConnectionManager(settings)
    messages: list[str] = []
    def capture(message: str) -> bool:
        messages.append(message)
        return True

    connections.send_alert = capture  # type: ignore[method-assign]
    now = datetime(2026, 8, 7, 15, 21, tzinfo=IST)

    PaperPositionMonitor(application, connections).run_cycle(now)
    restarted = PaperPositionMonitor(application, connections)
    restarted.run_cycle(now + timedelta(minutes=1))

    assert len(messages) == 1
    assert "Paper daily report" in messages[0]
    assert "Database integrity" in messages[0]
    assert restarted.snapshot().last_daily_report_at == now
