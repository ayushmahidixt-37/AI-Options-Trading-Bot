"""A short position that cannot be quoted must never fail silently.

Regression test for a production bug found 2026-08-27. `_check_strangle_exits`
quotes every leg before evaluating any exit condition, so when quoting raised
(the dashboard had been restarted and the Angel One connection was not
restored) the group was skipped and the force-exit check was never reached. A
live short strangle sat open through its force-exit time and overnight while
the monitor retried every 15 seconds, logging nothing.

A long position failing this way merely stalls with capped downside. A short
one keeps accumulating unbounded risk, so the failure must be loud.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from options_bot.config import Settings
from options_bot.connections import ConnectionActionError, ConnectionManager
from options_bot.domain import Instrument, PaperOrderRequest, Quote
from options_bot.paper_monitor import PaperPositionMonitor
from options_bot.runner import build_application

IST = ZoneInfo("Asia/Kolkata")


def _open_strangle(application, now):
    for symbol, token, option_type in (
        ("NIFTY01SEP2624400CE", "CE-T", "CE"),
        ("NIFTY01SEP2624250PE", "PE-T", "PE"),
    ):
        instrument = Instrument(symbol, token, "NFO", "NIFTY", option_type, 65)
        application.paper_broker.open_position(
            PaperOrderRequest(
                instrument=instrument,
                lots=1,
                quote=Quote(symbol, 100.0, now),
                stop_price=104.0,
                strategy="short-strangle-auto-paper",
                reason="fixture",
                side="SELL",
                trade_group_id="group-unexitable",
            ),
            now,
        )


def test_unquotable_short_position_raises_a_critical_alarm_at_force_exit(tmp_path) -> None:
    settings = Settings.from_env({
        "DATA_DIR": str(tmp_path),
        "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        "MAX_OPEN_POSITIONS": "5",
    })
    application = build_application(settings)
    connections = ConnectionManager(settings)
    entry_at = datetime(2026, 8, 26, 9, 50, tzinfo=IST)
    _open_strangle(application, entry_at)

    alerts: list[str] = []
    connections.send_alert = lambda message: alerts.append(message) or True  # type: ignore[method-assign]

    def broken_quote(_instrument, observed_at=None):
        raise ConnectionActionError("Angel One is not connected")

    connections.quote_instrument = broken_quote  # type: ignore[method-assign]
    monitor = PaperPositionMonitor(application, connections)

    # Before force-exit time: a quote failure is an ordinary error, not an alarm.
    monitor.run_cycle(datetime(2026, 8, 26, 11, 0, tzinfo=IST))
    assert not any("UNEXITED SHORT POSITION" in a for a in alerts)

    # Past force-exit time: the position still cannot be closed, and that must
    # be surfaced loudly rather than retried silently.
    snapshot = monitor.run_cycle(datetime(2026, 8, 26, 15, 25, tzinfo=IST))

    assert len(application.ledger.open_positions()) == 2  # still open -- cannot close without a quote
    assert snapshot.error is not None
    assert any("UNEXITED SHORT POSITION" in a for a in alerts), alerts
    events = application.ledger.recent_events()
    critical = [e for e in events if e["event_type"] == "strangle_force_exit_failed"]
    assert critical, [dict(e) for e in events]
    assert critical[0]["level"] == "CRITICAL"
