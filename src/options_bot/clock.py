"""Market-session checks with fail-closed entry behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import Settings


@dataclass(frozen=True)
class MarketClock:
    settings: Settings

    def entries_allowed(self, now: datetime) -> bool:
        local = now.astimezone(self.settings.timezone)
        return (
            local.weekday() < 5
            and local.date() not in self.settings.nse_holidays
            and self.settings.entry_start <= local.time() < self.settings.entry_cutoff
        )

    def force_exit_due(self, now: datetime) -> bool:
        local = now.astimezone(self.settings.timezone)
        return (
            local.weekday() < 5
            and local.date() not in self.settings.nse_holidays
            and local.time() >= self.settings.force_exit
        )

    def trading_date(self, now: datetime) -> str:
        return now.astimezone(self.settings.timezone).date().isoformat()
