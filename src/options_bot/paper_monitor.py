"""Guarded automatic paper entries and exits; never submits broker orders."""

from __future__ import annotations

import threading
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime

from .connections import ConnectionActionError, ConnectionManager, PaperTradeProposal
from .domain import Instrument, PaperOrderRequest
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
    auto_entry_enabled: bool = False
    auto_entry_last_action: str = "Automatic paper entries are disabled."
    last_daily_report_at: datetime | None = None


class PaperPositionMonitor:
    """Collect guarded entries and apply exits to simulated positions only."""

    def __init__(self, application: Application, connections: ConnectionManager) -> None:
        self.application = application
        self.connections = connections
        self._lock = threading.Lock()
        self._snapshot = PaperMonitorSnapshot()
        self._first_cycle = True
        self._auto_entry_enabled = (
            self.application.ledger.get_state("auto_paper_enabled", "false") == "true"
        )
        self._snapshot = replace(
            self._snapshot,
            auto_entry_enabled=self._auto_entry_enabled,
            auto_entry_last_action=(
                "Automatic paper entries restored as enabled after restart."
                if self._auto_entry_enabled
                else self._snapshot.auto_entry_last_action
            ),
            last_daily_report_at=(
                datetime.fromisoformat(last_report)
                if (last_report := self.application.ledger.get_state("last_daily_report_at"))
                else None
            ),
        )

    def set_auto_entry(self, enabled: bool, confirmation: str) -> PaperMonitorSnapshot:
        expected = "ENABLE AUTO PAPER" if enabled else "DISABLE AUTO PAPER"
        if confirmation != expected:
            raise ValueError(f"Type {expected} exactly")
        self._auto_entry_enabled = enabled
        self.application.ledger.set_state(
            "auto_paper_enabled", "true" if enabled else "false"
        )
        now = datetime.now(self.application.settings.timezone)
        action = "enabled" if enabled else "disabled"
        self.application.ledger.record_event(
            now.isoformat(), "WARNING" if enabled else "INFO", "auto_paper", action
        )
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                auto_entry_enabled=enabled,
                auto_entry_last_action=f"Automatic paper entries {action}.",
            )
            return self._snapshot

    def record_proposal_context(self, order_id: int, proposal: PaperTradeProposal) -> None:
        self.application.ledger.record_trade_journal(
            order_id,
            {
                "signal_at": proposal.signal_at.isoformat() if proposal.signal_at else None,
                "direction": proposal.direction,
                "nifty_spot": proposal.nifty_spot,
                "ema_fast": proposal.ema_fast,
                "ema_slow": proposal.ema_slow,
                "rsi_value": proposal.rsi_value,
                "atr_value": proposal.atr_value,
                "confidence": proposal.confidence,
                "expiry": (
                    proposal.instrument.expiry.isoformat()
                    if proposal.instrument.expiry
                    else None
                ),
                "strike": proposal.instrument.strike,
                "estimated_max_loss": proposal.estimated_max_loss,
                "data_warning": None,
            },
        )

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
                self.application.ledger.update_trade_excursion(
                    int(position["id"]), quote.price - float(position["entry_fill_price"])
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
        self.connections.record_paper_cycle(now)
        auto_action = self._maybe_auto_entry(now)
        report_at = self._maybe_daily_report(now)
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
                auto_entry_enabled=self._auto_entry_enabled,
                auto_entry_last_action=auto_action or self._snapshot.auto_entry_last_action,
                last_daily_report_at=report_at or self._snapshot.last_daily_report_at,
            )
            self._first_cycle = False
            return self._snapshot

    def _maybe_auto_entry(self, now: datetime) -> str | None:
        if not self._auto_entry_enabled:
            return None
        open_positions = self.application.ledger.open_positions()
        if len(open_positions) >= self.application.settings.max_open_positions:
            return (
                "Automatic paper entry waiting: open-position capacity "
                f"{len(open_positions)}/{self.application.settings.max_open_positions}"
            )
        block_reason = self.connections.entry_block_reason(now)
        if block_reason:
            return f"Automatic paper entry blocked: {block_reason}"
        connection = self.connections.snapshot()
        signal_at = connection.latest_candle_at
        if connection.signal_label not in {"BULLISH", "BEARISH"} or signal_at is None:
            return None
        signal_key = signal_at.isoformat()
        if self.application.ledger.get_state("auto_paper_last_signal") == signal_key:
            return None
        self.application.ledger.set_state("auto_paper_last_signal", signal_key)
        try:
            proposal = self.connections.create_paper_proposal(now)
            order_id = self.application.paper_broker.buy(
                PaperOrderRequest(
                    instrument=proposal.instrument,
                    lots=1,
                    quote=proposal.quote,
                    stop_price=proposal.stop_price,
                    strategy="momentum-v1-auto-paper",
                    reason=f"Auto-paper {proposal.direction} signal",
                ),
                now,
            )
            self.record_proposal_context(order_id, proposal)
            detail = f"Opened {proposal.instrument.symbol} as paper order {order_id}"
            self.application.ledger.record_event(
                now.isoformat(), "INFO", "auto_paper_entry", detail
            )
            try:
                self.connections.send_alert(f"Automatic paper entry: {detail}")
            except Exception:
                self.application.ledger.record_event(
                    now.isoformat(),
                    "WARNING",
                    "telegram_alert_failed",
                    "Automatic paper entry was opened but its Telegram alert failed",
                )
            return detail
        except (ConnectionActionError, RuntimeError, ValueError) as exc:
            detail = f"Automatic paper entry rejected: {exc}"
            self.application.ledger.record_event(
                now.isoformat(), "INFO", "auto_paper_rejected", detail
            )
            return detail

    def _maybe_daily_report(self, now: datetime) -> datetime | None:
        if now.time() < self.application.settings.force_exit:
            return None
        trading_date = self.application.clock.trading_date(now)
        if self.application.ledger.get_state("last_daily_report_date") == trading_date:
            return None
        self.application.ledger.set_state("last_daily_report_date", trading_date)
        self.application.ledger.set_state("last_daily_report_at", now.isoformat())
        summary = self.application.ledger.daily_summary(trading_date)
        connection = self.connections.snapshot()
        message = (
            f"Paper daily report · {trading_date}\n"
            f"Trades {summary['trades']} · Wins {summary['wins']} · "
            f"Losses {summary['losses']}\n"
            f"Net P&L {float(summary['net_pnl']):.2f} · "
            f"Fees {float(summary['fees']):.2f}\n"
            f"Open positions {summary['open_positions']} · "
            f"Archive gaps {connection.archive_gaps}\n"
            f"Reconnects {connection.reconnect_count} · "
            f"Monitor failures {connection.consecutive_failures}\n"
            f"Database integrity {connection.archive_integrity}. Paper mode only."
        )
        try:
            sent = self.connections.send_alert(message)
            self.application.ledger.record_event(
                now.isoformat(),
                "INFO" if sent else "WARNING",
                "daily_report" if sent else "daily_report_skipped",
                "Telegram daily report sent"
                if sent
                else "Telegram credentials unavailable; daily report not sent",
            )
        except Exception:
            self.application.ledger.record_event(
                now.isoformat(),
                "WARNING",
                "daily_report_failed",
                "Telegram daily report failed; duplicate send suppressed",
            )
        return now

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
