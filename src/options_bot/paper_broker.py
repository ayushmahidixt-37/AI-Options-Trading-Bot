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

    def buy(self, request: PaperOrderRequest, now: datetime) -> int:
        self.risk.validate(request, now)
        fill = self._buy_fill(request.quote.price)
        fee = self.settings.paper_fee_per_order
        order_id = self.ledger.insert_open(
            {
                "client_order_id": str(uuid.uuid4()),
                "trading_date": self.clock.trading_date(now),
                "created_at": now.isoformat(),
                "symbol": request.instrument.symbol,
                "token": request.instrument.token,
                "exchange_name": request.instrument.exchange,
                "underlying": request.instrument.underlying,
                "option_type": request.instrument.option_type,
                "side": "BUY",
                "lots": request.lots,
                "lot_size": request.instrument.lot_size,
                "units": request.units,
                "requested_price": request.quote.price,
                "entry_fill_price": fill,
                "stop_price": request.stop_price,
                "entry_fee": fee,
                "status": "OPEN",
                "strategy": request.strategy,
                "reason": request.reason,
            }
        )
        with self.ledger.connect() as con:
            con.execute("UPDATE paper_account SET fees_paid=fees_paid+? WHERE id=1", (fee,))
        return order_id

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
        fill = self._sell_fill(quote.price)
        fee = self.settings.paper_fee_per_order
        gross = (fill - float(position["entry_fill_price"])) * int(position["units"])
        pnl = round(gross - float(position["entry_fee"]) - fee, 2)
        self.ledger.close(order_id, at=now.isoformat(), price=fill, fee=fee, pnl=pnl, reason=reason)
        return pnl
