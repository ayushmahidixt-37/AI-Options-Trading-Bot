"""Tests for the live (paper-only) short-strangle execution path added
2026-08-25 -- proposal building in connections.py and the daily entry /
paired-exit orchestration in paper_monitor.py. Mirrors the existing
test_connections.py / test_paper_monitor.py fixture style.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import options_bot.connections
from options_bot.config import Settings
from options_bot.connections import (
    NIFTY_TOKEN,
    SHORT_STRANGLE_ENTRY_TIME,
    ConnectionActionError,
    ConnectionManager,
    ShortStrangleLegProposal,
    ShortStrangleProposal,
)
from options_bot.domain import Instrument, Quote
from options_bot.paper_monitor import PaperPositionMonitor
from options_bot.runner import build_application

IST = ZoneInfo("Asia/Kolkata")


def settings(tmp_path: Path, credentials_path: Path) -> Settings:
    return Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials_path),
            "MAX_OPEN_POSITIONS": "5",
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


def _seed_opening_range(manager: ConnectionManager, today: date, wide: bool = False) -> None:
    base = datetime(today.year, today.month, today.day, 9, 15, tzinfo=IST)
    high = 24900 if wide else 24625
    low = 24500 if wide else 24615
    with manager.archive.connect() as con:
        for index in range(6):
            started_at = base + timedelta(minutes=5 * index)
            con.execute(
                """INSERT INTO market_candles(
                       instrument_token, symbol, exchange_name, timeframe, started_at,
                       open, high, low, close, source, collected_at
                   ) VALUES (?, 'NIFTY', 'NSE', 'FIVE_MINUTE', ?, 24620, ?, ?, 24620,
                             'angel-one', ?)""",
                (NIFTY_TOKEN, started_at.isoformat(), high, low, started_at.isoformat()),
            )


def _seed_strangle_instruments(manager: ConnectionManager, expiry: date, now: datetime) -> None:
    manager.archive.save_instruments(
        [
            Instrument("NIFTY13AUG2624700CE", "CE-TOKEN", "NFO", "NIFTY", "CE", 75, expiry, 24700),
            Instrument("NIFTY13AUG2624500PE", "PE-TOKEN", "NFO", "NIFTY", "PE", 75, expiry, 24500),
        ],
        now,
    )


def _ready_manager(tmp_path: Path, now: datetime) -> ConnectionManager:
    credentials = credential_file(tmp_path)

    class FakeApi:
        def generateSession(self, *_args):
            return {"status": True, "data": {"jwtToken": "secret"}}

        def getMarketData(self, *_args):
            return {"status": True, "data": {"fetched": [{"ltp": 120.0}]}}

    manager = ConnectionManager(
        settings(tmp_path, credentials),
        smart_api_factory=lambda _key: FakeApi(),
        totp_factory=lambda _secret: "123456",
        instrument_master_loader=lambda: [],
    )
    manager.connect_angel()
    manager._snapshot = replace(
        manager.snapshot(),
        nifty_price=24620,
        data_status="fresh",
        latest_candle_at=now - timedelta(minutes=5),
    )
    _seed_opening_range(manager, now.date())
    _seed_strangle_instruments(manager, date(2026, 8, 13), now)
    return manager


def _revive(monkeypatch) -> None:
    """Turn the 2026-08-26 retirement guard off for one test.

    The strangle is retired (see connections.SHORT_STRANGLE_RETIRED) because
    the account cannot cover its margin, not because the implementation is
    wrong -- so the execution path is kept working and kept under test. These
    tests exercise that path directly; the guard itself is covered by
    test_create_short_strangle_proposal_refuses_while_retired below.
    """
    monkeypatch.setattr(options_bot.connections, "SHORT_STRANGLE_RETIRED", False)


def test_create_short_strangle_proposal_refuses_while_retired(tmp_path: Path) -> None:
    """The retirement guard must block every entry point, including a fully
    valid setup that would otherwise produce a proposal."""
    now = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    manager = _ready_manager(tmp_path, now)

    try:
        manager.create_short_strangle_proposal(now)
    except ConnectionActionError as exc:
        assert "retired" in str(exc).lower()
        assert "margin" in str(exc).lower()
    else:
        raise AssertionError("a proposal was created while the strategy is retired")


def test_create_short_strangle_proposal_selects_matching_otm_legs(tmp_path: Path, monkeypatch) -> None:
    _revive(monkeypatch)
    now = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    manager = _ready_manager(tmp_path, now)

    proposal = manager.create_short_strangle_proposal(now)

    assert proposal.call.instrument.option_type == "CE"
    assert proposal.call.instrument.strike == 24700
    assert proposal.put.instrument.option_type == "PE"
    assert proposal.put.instrument.strike == 24500
    assert proposal.premium_collected == 240.0
    assert proposal.call.stop_price == 124.0  # 120 + SHORT_STRANGLE_RISK_BUDGET_PER_LEG / lot_size
    assert proposal.call.instrument.token != proposal.put.instrument.token


def test_create_short_strangle_proposal_rejects_before_entry_time(tmp_path: Path, monkeypatch) -> None:
    _revive(monkeypatch)
    now = datetime(2026, 8, 6, 9, 30, tzinfo=IST)
    assert now.time() < SHORT_STRANGLE_ENTRY_TIME
    manager = _ready_manager(tmp_path, now)

    try:
        manager.create_short_strangle_proposal(now)
    except ConnectionActionError as exc:
        assert "early" in str(exc).lower()
    else:
        raise AssertionError("proposal was created before the configured entry time")


def test_create_short_strangle_proposal_rejects_wide_opening_range(tmp_path: Path, monkeypatch) -> None:
    _revive(monkeypatch)
    now = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    manager = _ready_manager(tmp_path, now)
    with manager.archive.connect() as con:
        con.execute("DELETE FROM market_candles WHERE timeframe='FIVE_MINUTE'")
    _seed_opening_range(manager, now.date(), wide=True)

    try:
        manager.create_short_strangle_proposal(now)
    except ConnectionActionError as exc:
        assert "opening range" in str(exc).lower()
    else:
        raise AssertionError("proposal was created despite a too-wide opening range")


def _application_and_monitor(tmp_path: Path):
    settings_obj = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "MAX_OPEN_POSITIONS": "5",
        }
    )
    application = build_application(settings_obj)
    connections = ConnectionManager(settings_obj)
    monitor = PaperPositionMonitor(application, connections)
    return application, connections, monitor


def _fixture_proposal(now: datetime) -> ShortStrangleProposal:
    call_instrument = Instrument(
        "NIFTY13AUG2624700CE", "CE-TOKEN", "NFO", "NIFTY", "CE", 75, now.date(), 24700
    )
    put_instrument = Instrument(
        "NIFTY13AUG2624500PE", "PE-TOKEN", "NFO", "NIFTY", "PE", 75, now.date(), 24500
    )
    call_quote = Quote(call_instrument.symbol, 120.0, now)
    put_quote = Quote(put_instrument.symbol, 120.0, now)
    return ShortStrangleProposal(
        trade_group_id="group-1",
        call=ShortStrangleLegProposal(call_instrument, call_quote, 124.0),
        put=ShortStrangleLegProposal(put_instrument, put_quote, 124.0),
        premium_collected=240.0,
        nifty_spot=24620,
    )


def test_automatic_short_strangle_entry_opens_both_legs_once_per_day(tmp_path: Path) -> None:
    application, connections, monitor = _application_and_monitor(tmp_path)
    now = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    proposal = _fixture_proposal(now)
    connections.create_short_strangle_proposal = lambda _now=None: proposal  # type: ignore[method-assign]
    connections.quote_instrument = lambda instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 120.0, observed_at or now
    )

    try:
        monitor.set_auto_strangle_entry(True, "wrong")
    except ValueError:
        pass
    else:
        raise AssertionError("automatic short-strangle entry accepted incorrect confirmation")
    monitor.set_auto_strangle_entry(True, "ENABLE AUTO STRANGLE")

    first = monitor.run_cycle(now)
    second = monitor.run_cycle(now + timedelta(minutes=5))

    positions = application.ledger.open_positions()
    assert len(positions) == 2
    assert {row["side"] for row in positions} == {"SELL"}
    assert {row["trade_group_id"] for row in positions} == {"group-1"}
    assert "Opened short strangle" in first.auto_strangle_last_action
    assert application.ledger.get_state("auto_strangle_last_entry_date") == "2026-08-06"
    # Second cycle same day must not open a duplicate pair.
    assert len(application.ledger.open_positions()) == 2
    assert second.auto_strangle_last_action == first.auto_strangle_last_action


def test_short_strangle_rolls_back_call_leg_when_put_leg_fails(tmp_path: Path) -> None:
    application, connections, monitor = _application_and_monitor(tmp_path)
    now = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    call_instrument = Instrument(
        "NIFTY13AUG2624700CE", "CE-TOKEN", "NFO", "NIFTY", "CE", 75, now.date(), 24700
    )
    put_instrument = Instrument(
        "NIFTY13AUG2624500PE", "PE-TOKEN", "NFO", "NIFTY", "PE", 75, now.date(), 24500
    )
    call_quote = Quote(call_instrument.symbol, 120.0, now)
    # An invalid put stop (below its own quote price) makes risk.py reject
    # the put leg -- the call leg must not be left open as an orphan.
    put_quote = Quote(put_instrument.symbol, 120.0, now)
    broken_proposal = ShortStrangleProposal(
        trade_group_id="group-2",
        call=ShortStrangleLegProposal(call_instrument, call_quote, 124.0),
        put=ShortStrangleLegProposal(put_instrument, put_quote, 100.0),  # invalid: below quote
        premium_collected=240.0,
        nifty_spot=24620,
    )
    connections.create_short_strangle_proposal = lambda _now=None: broken_proposal  # type: ignore[method-assign]
    connections.quote_instrument = lambda instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 120.0, observed_at or now
    )
    monitor.set_auto_strangle_entry(True, "ENABLE AUTO STRANGLE")

    snapshot = monitor.run_cycle(now)

    assert application.ledger.open_positions() == []
    assert "rejected" in snapshot.auto_strangle_last_action.lower()
    assert application.ledger.get_state("auto_strangle_last_entry_date") == ""


def test_short_strangle_paired_exit_closes_both_legs_on_combined_stop(tmp_path: Path) -> None:
    application, connections, monitor = _application_and_monitor(tmp_path)
    now = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    proposal = _fixture_proposal(now)
    connections.create_short_strangle_proposal = lambda _now=None: proposal  # type: ignore[method-assign]
    connections.quote_instrument = lambda instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 120.0, observed_at or now
    )
    monitor.set_auto_strangle_entry(True, "ENABLE AUTO STRANGLE")
    monitor.run_cycle(now)
    assert len(application.ledger.open_positions()) == 2

    # Combined buy-back cost of 500 (250+250) vs premium collected ~239.88
    # (120 entry fills each side, minus slippage) is well past 2x -- stop.
    connections.quote_instrument = lambda instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 250.0, observed_at or now
    )

    later = now + timedelta(minutes=30)
    snapshot = monitor.run_cycle(later)

    assert application.ledger.open_positions() == []
    assert snapshot.positions_closed == 2
    assert "strangle-stop" in snapshot.last_action


def test_short_strangle_paired_exit_closes_both_legs_at_force_exit(tmp_path: Path) -> None:
    application, connections, monitor = _application_and_monitor(tmp_path)
    now = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    proposal = _fixture_proposal(now)
    connections.create_short_strangle_proposal = lambda _now=None: proposal  # type: ignore[method-assign]
    connections.quote_instrument = lambda instrument, observed_at=None: Quote(  # type: ignore[method-assign]
        instrument.symbol, 120.0, observed_at or now
    )
    monitor.set_auto_strangle_entry(True, "ENABLE AUTO STRANGLE")
    monitor.run_cycle(now)
    assert len(application.ledger.open_positions()) == 2

    force_exit_time = datetime(2026, 8, 6, 15, 21, tzinfo=IST)
    snapshot = monitor.run_cycle(force_exit_time)

    assert application.ledger.open_positions() == []
    assert snapshot.positions_closed == 2
    assert "strangle-force-exit" in snapshot.last_action
