"""UI-safe paper actions for local operator-triggered workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .domain import Quote
from .health import healthcheck
from .runner import Application


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


def status_snapshot(application: Application) -> dict[str, object]:
    """Return the current paper account and positions as plain dictionaries."""
    return {
        "account": dict(application.ledger.account()),
        "positions": [dict(row) for row in application.ledger.open_positions()],
    }


def run_health_action(application: Application) -> ActionResult:
    report = healthcheck(application.settings, application.ledger)
    return ActionResult(report.ok, "Healthcheck passed" if report.ok else "Healthcheck failed")


def run_paper_scan_action(application: Application) -> ActionResult:
    """Perform a safe scan placeholder without placing an order.

    The dashboard now has read-only NIFTY quotes and five-minute analysis, but
    option discovery and a confirmed paper-entry workflow are not wired yet.
    This action intentionally does not buy anything.
    """
    report = healthcheck(application.settings, application.ledger)
    if not report.ok:
        return ActionResult(False, "Paper scan blocked because healthcheck failed")
    return ActionResult(
        True,
        "Paper scan is ready, but option discovery is not wired yet; no order was placed.",
    )


def close_paper_position_action(
    application: Application, *, order_id: int, exit_price: float, observed_at: datetime, reason: str
) -> ActionResult:
    rows = {int(row["id"]): row for row in application.ledger.open_positions()}
    if order_id not in rows:
        return ActionResult(False, "Paper position is not open")
    if exit_price <= 0:
        return ActionResult(False, "Exit price must be positive")
    position = rows[order_id]
    pnl = application.paper_broker.close(
        order_id,
        Quote(str(position["symbol"]), exit_price, observed_at),
        observed_at,
        reason or "ui-close",
    )
    return ActionResult(True, f"Closed paper position {order_id} with PnL {pnl:.2f}")
