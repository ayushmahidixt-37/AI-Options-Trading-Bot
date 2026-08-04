"""Application composition for the server-side paper engine."""

from __future__ import annotations

from dataclasses import dataclass

from .clock import MarketClock
from .config import Settings
from .ledger import PaperLedger
from .paper_broker import PaperBroker
from .risk import RiskEngine


@dataclass
class Application:
    settings: Settings
    ledger: PaperLedger
    clock: MarketClock
    risk: RiskEngine
    paper_broker: PaperBroker


def build_application(settings: Settings) -> Application:
    ledger = PaperLedger(settings.database_path)
    ledger.initialize()
    ledger.create_account(settings.starting_capital)
    clock = MarketClock(settings)
    risk = RiskEngine(settings, ledger, clock)
    broker = PaperBroker(settings, ledger, risk, clock)
    return Application(settings, ledger, clock, risk, broker)
