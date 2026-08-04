"""Paper-account reporting without broker synchronization."""

from __future__ import annotations

from dataclasses import dataclass

from .ledger import PaperLedger


@dataclass(frozen=True)
class AccountReport:
    starting_capital: float
    realized_pnl: float
    fees_paid: float
    open_positions: int

    @property
    def realized_equity(self) -> float:
        return self.starting_capital + self.realized_pnl


def account_report(ledger: PaperLedger) -> AccountReport:
    account = ledger.account()
    return AccountReport(
        starting_capital=float(account["starting_capital"]),
        realized_pnl=float(account["realized_pnl"]),
        fees_paid=float(account["fees_paid"]),
        open_positions=len(ledger.open_positions()),
    )
