"""Centralized pre-trade checks for paper orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clock import MarketClock
from .config import Settings
from .domain import PaperOrderRequest
from .ledger import PaperLedger


class RiskRejected(RuntimeError):
    """Raised when a proposed paper order violates a risk invariant."""


@dataclass
class RiskEngine:
    settings: Settings
    ledger: PaperLedger
    clock: MarketClock

    def validate(self, request: PaperOrderRequest, now: datetime) -> None:
        if request.quote.symbol != request.instrument.symbol:
            raise RiskRejected("Quote symbol does not match instrument")
        if not self.clock.entries_allowed(now):
            raise RiskRejected("New entries are outside the configured window")
        age = (now - request.quote.observed_at).total_seconds()
        if age < 0 or age > self.settings.max_quote_age_seconds:
            raise RiskRejected("Quote is stale or timestamped in the future")
        if request.lots > self.settings.max_lots_per_trade:
            raise RiskRejected("Lot limit exceeded")
        if request.stop_price <= 0 or request.stop_price >= request.quote.price:
            raise RiskRejected("Long-option stop must be positive and below entry")
        expected_fill = request.quote.price * (
            1 + self.settings.paper_slippage_bps / 10_000
        )
        loss_at_stop = (
            (expected_fill - request.stop_price) * request.units
            + 2 * self.settings.paper_fee_per_order
        )
        if loss_at_stop > self.settings.max_loss_per_trade:
            raise RiskRejected("Maximum loss per trade exceeded")
        positions = self.ledger.open_positions()
        if len(positions) >= self.settings.max_open_positions:
            raise RiskRejected("Open-position limit reached")
        if any(row["symbol"] == request.instrument.symbol for row in positions):
            raise RiskRejected("Duplicate open symbol")
        trading_date = self.clock.trading_date(now)
        if self.ledger.trades_on(trading_date) >= self.settings.max_trades_per_day:
            raise RiskRejected("Daily trade limit reached")
        account = self.ledger.account()
        if float(account["realized_pnl"]) <= -self.settings.max_daily_net_loss:
            raise RiskRejected("Daily loss circuit breaker is latched")
        used = sum(
            float(row["entry_fill_price"]) * int(row["units"])
            + float(row["entry_fee"])
            for row in positions
        )
        required = expected_fill * request.units + self.settings.paper_fee_per_order
        available = float(account["starting_capital"]) + float(account["realized_pnl"]) - used
        if required > available:
            raise RiskRejected("Insufficient paper capital")
