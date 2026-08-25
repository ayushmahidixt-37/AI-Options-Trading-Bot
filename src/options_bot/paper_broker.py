"""Deterministic, conservative paper fills; contains no broker order API."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from .clock import MarketClock
from .config import Settings
from .domain import PaperOrderRequest, Quote
from .ledger import PaperLedger
from .risk import RiskEngine


@dataclass
class PaperBroker:
    settings: Settings
    ledger: PaperLedger
    risk: RiskEngine
    clock: MarketClock

    def _buy_fill(self, price: float) -> float:
        return round(price * (1 + self.settings.paper_slippage_bps / 10_000), 2)

    def _sell_fill(self, price: float) -> float:
        return round(price * (1 - self.settings.paper_slippage_bps / 10_000), 2)

    def open_position(self, request: PaperOrderRequest, now: datetime) -> int:
        """Open a position, BUY (long, the original/default) or SELL (short --
        added 2026-08-25 for the short strangle). A SELL's entry fill is
        adverse the same way a BUY's is (you receive slightly less than
        quote when selling, just as you pay slightly more when buying).
        """
        self.risk.validate(request, now)
        fill = self._buy_fill(request.quote.price) if request.side == "BUY" else self._sell_fill(request.quote.price)
        fee = self.settings.paper_fee_per_order
        order_id = self.ledger.insert_open_with_fee(
            {
                "client_order_id": str(uuid.uuid4()),
                "trading_date": self.clock.trading_date(now),
                "created_at": now.isoformat(),
                "symbol": request.instrument.symbol,
                "token": request.instrument.token,
                "exchange_name": request.instrument.exchange,
                "underlying": request.instrument.underlying,
                "option_type": request.instrument.option_type,
                "side": request.side,
                "trade_group_id": request.trade_group_id,
                "lots": request.lots,
                "lot_size": request.instrument.lot_size,
                "units": request.units,
                "requested_price": request.quote.price,
                "entry_fill_price": fill,
                "stop_price": request.stop_price,
                "target_price": request.target_price,
                "entry_fee": fee,
                "status": "OPEN",
                "strategy": request.strategy,
                "reason": request.reason,
            },
            fee,
        )
        return order_id

    # Kept as an alias -- every existing call site (Candidate B's long-only
    # entries) reads clearly as "buy", and BUY is still open_position's
    # default side. Only new SELL-side code needs to call open_position
    # directly with side="SELL".
    def buy(self, request: PaperOrderRequest, now: datetime) -> int:
        return self.open_position(request, now)

    def close(self, order_id: int, quote: Quote, now: datetime, reason: str) -> float:
        rows = {int(row["id"]): row for row in self.ledger.open_positions()}
        if order_id not in rows:
            raise RuntimeError("Paper position is not open")
        position = rows[order_id]
        if quote.symbol != position["symbol"]:
            raise ValueError("Quote symbol does not match paper position")
        age = (now - quote.observed_at).total_seconds()
        if age < 0 or age > self.settings.max_quote_age_seconds:
            raise ValueError("Exit quote is stale or timestamped in the future")
        side = position["side"] if "side" in position.keys() else "BUY"
        fee = self.settings.paper_fee_per_order
        if side == "SELL":
            # Closing a short means buying it back -- adverse fill is now
            # paying slightly *more* than quote, and profit comes from
            # buying back cheaper than the entry premium collected.
            fill = self._buy_fill(quote.price)
            gross = (float(position["entry_fill_price"]) - fill) * int(position["units"])
        else:
            fill = self._sell_fill(quote.price)
            gross = (fill - float(position["entry_fill_price"])) * int(position["units"])
        pnl = round(gross - float(position["entry_fee"]) - fee, 2)
        self.ledger.close(order_id, at=now.isoformat(), price=fill, fee=fee, pnl=pnl, reason=reason)
        return pnl
