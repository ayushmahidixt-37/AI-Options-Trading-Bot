"""Local service health reporting."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass

from .config import Settings
from .ledger import PaperLedger


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    checks: dict[str, str]


def healthcheck(settings: Settings, ledger: PaperLedger) -> HealthReport:
    checks: dict[str, str] = {"mode": settings.trading_mode, "auto_start": str(settings.auto_start).lower()}
    ok = True
    try:
        with ledger.connect() as con:
            con.execute("SELECT 1").fetchone()
        checks["database"] = "ok"
    except (OSError, sqlite3.Error):
        checks["database"] = "failed"
        ok = False
    usage = shutil.disk_usage(settings.data_dir)
    checks["disk_free_mb"] = str(usage.free // (1024 * 1024))
    if usage.free < 100 * 1024 * 1024:
        ok = False
        checks["disk"] = "low"
    else:
        checks["disk"] = "ok"
    return HealthReport(ok=ok, checks=checks)
