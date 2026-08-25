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
        is_sell = request.side == "SELL"
        if request.stop_price <= 0:
            raise RiskRejected("Stop must be positive")
        if is_sell and request.stop_price <= request.quote.price:
            raise RiskRejected("Short-option stop must be above entry")
        if not is_sell and request.stop_price >= request.quote.price:
            raise RiskRejected("Long-option stop must be below entry")
        expected_fill = request.quote.price * (
            1 - self.settings.paper_slippage_bps / 10_000
            if is_sell
            else 1 + self.settings.paper_slippage_bps / 10_000
        )
        # loss_at_stop already accounts for direction via PaperOrderRequest's
        # own side-aware risk_at_stop -- recomputed here against the
        # *expected* (slippage-adjusted) fill rather than the raw quote,
        # matching the original BUY-only formula's intent.
        loss_at_stop = (
            (request.stop_price - expected_fill) * request.units
            if is_sell
            else (expected_fill - request.stop_price) * request.units
        ) + 2 * self.settings.paper_fee_per_order
        if loss_at_stop > self.settings.max_loss_per_trade:
            raise RiskRejected("Maximum loss per trade exceeded")
        positions = self.ledger.open_positions()
        if len(positions) >= self.settings.max_open_positions:
            raise RiskRejected("Open-position limit reached")
        if any(row["symbol"] == request.instrument.symbol for row in positions):
            raise RiskRejected("Duplicate open symbol")
        trading_date = self.clock.trading_date(now)
        if (
            self.settings.max_trades_per_day > 0
            and self.ledger.trades_on(trading_date) >= self.settings.max_trades_per_day
        ):
            raise RiskRejected("Daily trade limit reached")
        account = self.ledger.account()
        if (
            self.settings.max_daily_net_loss > 0
            and self.ledger.realized_pnl_on(trading_date) <= -self.settings.max_daily_net_loss
        ):
            raise RiskRejected("Daily loss circuit breaker is latched")
        # Capital "used" by each open position: a BUY commits the premium
        # paid; a SELL (short) doesn't pay any premium upfront (it receives
        # one), but real exchange margin for holding a naked short is
        # several times the premium and isn't modeled here (see
        # short_premium_backtest.py's docstring) -- treating its own
        # max-loss-at-stop as the capital commitment is a deliberately
        # conservative stand-in for margin, so the paper account can never
        # "use" more capital than it could actually lose.
        def position_capital(row: object) -> float:
            if row["side"] == "SELL":
                stop = float(row["stop_price"])
                entry = float(row["entry_fill_price"])
                return max(0.0, stop - entry) * int(row["units"]) + float(row["entry_fee"])
            return float(row["entry_fill_price"]) * int(row["units"]) + float(row["entry_fee"])

        used = sum(position_capital(row) for row in positions)
        required = (
            loss_at_stop
            if is_sell
            else expected_fill * request.units + self.settings.paper_fee_per_order
        )
        available = float(account["starting_capital"]) + float(account["realized_pnl"]) - used
        if required > available:
            raise RiskRejected("Insufficient paper capital")
