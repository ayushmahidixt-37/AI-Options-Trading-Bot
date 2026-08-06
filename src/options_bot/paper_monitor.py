"""Automatic exits for confirmed paper positions; never creates entries."""

from __future__ import annotations

import threading
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime

from .connections import ConnectionActionError, ConnectionManager
from .domain import Instrument
from .runner import Application


@dataclass(frozen=True)
class PaperMonitorSnapshot:
    status: str = "waiting"
    last_run_at: datetime | None = None
    positions_checked: int = 0
    positions_closed: int = 0
    last_action: str = "No confirmed paper position has required an exit."
    error: str | None = None
    total_unrealized_pnl: float = 0.0
    recovered_positions: int = 0
    position_quotes: tuple[dict[str, object], ...] = ()


class PaperPositionMonitor:
    """Apply stop, reversal, and forced-exit rules to paper positions only."""

    def __init__(self, application: Application, connections: ConnectionManager) -> None:
        self.application = application
        self.connections = connections
        self._lock = threading.Lock()
        self._snapshot = PaperMonitorSnapshot()
        self._first_cycle = True

    def snapshot(self) -> PaperMonitorSnapshot:
        with self._lock:
            return self._snapshot

    def run_cycle(self, observed_at: datetime | None = None) -> PaperMonitorSnapshot:
        now = (observed_at or datetime.now(self.application.settings.timezone)).astimezone(
            self.application.settings.timezone
        )
        positions = self.application.ledger.open_positions()
        closed = 0
        actions: list[str] = []
        errors: list[str] = []
        position_quotes: list[dict[str, object]] = []
        signal = self.connections.snapshot().signal_label
        for position in positions:
            instrument = Instrument(
                symbol=str(position["symbol"]),
                token=str(position["token"]),
                exchange=str(position["exchange_name"]),
                underlying=str(position["underlying"]),
                option_type=str(position["option_type"]),
                lot_size=int(position["lot_size"]),
            )
            try:
                quote = self.connections.quote_instrument(instrument, now)
                sell_fill = round(
                    quote.price
                    * (1 - self.application.settings.paper_slippage_bps / 10_000),
                    2,
                )
                unrealized = round(
                    (sell_fill - float(position["entry_fill_price"]))
                    * int(position["units"])
                    - float(position["entry_fee"])
                    - self.application.settings.paper_fee_per_order,
                    2,
                )
                position_quotes.append(
                    {
                        "id": int(position["id"]),
                        "symbol": instrument.symbol,
                        "ltp": quote.price,
                        "unrealized_pnl": unrealized,
                        "stop_price": float(position["stop_price"]),
                        "quoted_at": quote.observed_at,
                    }
                )
                reason = self._exit_reason(position, quote.price, signal, now)
                if reason is None:
                    continue
                pnl = self.application.paper_broker.close(
                    int(position["id"]), quote, now, reason
                )
                closed += 1
                actions.append(f"{instrument.symbol} closed ({reason}), P&L {pnl:.2f}")
                self.application.ledger.record_event(
                    now.isoformat(), "INFO", "paper_exit", actions[-1]
                )
                try:
                    self.connections.send_alert(f"Paper exit: {actions[-1]}")
                except Exception:
                    errors.append(f"{instrument.symbol}: Telegram exit alert failed")
            except (ConnectionActionError, RuntimeError, ValueError) as exc:
                errors.append(f"{instrument.symbol}: {exc}")
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                status="error" if errors else "running",
                last_run_at=now,
                positions_checked=len(positions),
                positions_closed=closed,
                last_action="; ".join(actions) or self._snapshot.last_action,
                error="; ".join(errors[:3]) or None,
                total_unrealized_pnl=round(
                    sum(float(item["unrealized_pnl"]) for item in position_quotes), 2
                ),
                recovered_positions=(
                    len(positions)
                    if self._first_cycle
                    else self._snapshot.recovered_positions
                ),
                position_quotes=tuple(position_quotes),
            )
            self._first_cycle = False
            return self._snapshot

    def close_all(
        self, confirmation: str, observed_at: datetime | None = None
    ) -> PaperMonitorSnapshot:
        if confirmation != "CLOSE ALL PAPER":
            raise ValueError("Type CLOSE ALL PAPER exactly")
        now = (observed_at or datetime.now(self.application.settings.timezone)).astimezone(
            self.application.settings.timezone
        )
        positions = self.application.ledger.open_positions()
        for position in positions:
            instrument = Instrument(
                str(position["symbol"]),
                str(position["token"]),
                str(position["exchange_name"]),
                str(position["underlying"]),
                str(position["option_type"]),
                int(position["lot_size"]),
            )
            quote = self.connections.quote_instrument(instrument, now)
            self.application.paper_broker.close(
                int(position["id"]), quote, now, "paper-kill-switch"
            )
            self.application.ledger.record_event(
                now.isoformat(), "WARNING", "paper_kill_switch", instrument.symbol
            )
        snapshot = self.run_cycle(now)
        with self._lock:
            self._snapshot = replace(
                snapshot,
                positions_checked=len(positions),
                positions_closed=len(positions),
                last_action=f"Kill switch closed {len(positions)} paper position(s).",
            )
            return self._snapshot

    def _exit_reason(
        self, position: sqlite3.Row, price: float, signal: str, now: datetime
    ) -> str | None:
        if price <= float(position["stop_price"]):
            return "paper-stop"
        if self.application.clock.force_exit_due(now):
            return "paper-force-exit"
        option_type = str(position["option_type"])
        if (option_type == "CE" and signal == "BEARISH") or (
            option_type == "PE" and signal == "BULLISH"
        ):
            return "paper-signal-reversal"
        return None
