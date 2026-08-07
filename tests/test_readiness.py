from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from options_bot.config import Settings
from options_bot.connections import ConnectionSnapshot
from options_bot.market_archive import ArchiveStats, ReadinessMetrics
from options_bot.readiness import (
    build_readiness_report,
    export_readiness_csv,
    save_manual_review,
)

IST = ZoneInfo("Asia/Kolkata")


class ReadyLedger:
    def __init__(self) -> None:
        self.state = {f"readiness_{key}": "true" for key in (
            "broker_restrictions", "recovery_drill", "user_acceptance"
        )}

    def performance_summary(self) -> dict[str, object]:
        return {"trades": 100, "max_drawdown": 1000}

    def open_positions(self) -> list[object]:
        return []

    def get_state(self, key: str, default: str = "") -> str:
        return self.state.get(key, default)


class ReadyArchive:
    def stats(self) -> ArchiveStats:
        return ArchiveStats(1000, 500, 500, 20, None, None, 1, 0, "ok")

    def readiness_metrics(self) -> ReadinessMetrics:
        return ReadinessMetrics(20, 500, 100, 0)


def test_readiness_report_can_complete_without_enabling_live_trading(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.env"
    credentials.write_text("placeholder=true\n", encoding="utf-8")
    credentials.chmod(0o600)
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
            "CREDENTIALS_PATH": str(credentials),
        }
    )
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "market-data-20260807.sqlite3").touch()
    now = datetime(2026, 8, 7, 15, 21, tzinfo=IST)
    report = build_readiness_report(
        settings,
        ReadyLedger(),  # type: ignore[arg-type]
        ReadyArchive(),  # type: ignore[arg-type]
        ConnectionSnapshot(monitor_last_run_at=now, consecutive_failures=0),
        web_password="strong-local-password",
        observed_at=now,
    )

    assert report.status == "PAPER REVIEW COMPLETE"
    assert report.passed == report.total
    assert settings.live_trading_enabled is False
    exported = export_readiness_csv(report, tmp_path / "readiness.csv")
    assert "live_trading_approved,false" in exported.read_text(encoding="utf-8")


def test_manual_review_requires_exact_confirmation(tmp_path: Path) -> None:
    from options_bot.runner import build_application

    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    ledger = build_application(settings).ledger
    now = datetime(2026, 8, 7, 12, 0, tzinfo=IST)

    with pytest.raises(ValueError, match="SAVE PAPER REVIEW"):
        save_manual_review(ledger, confirmation="wrong", values={}, observed_at=now)

    save_manual_review(
        ledger,
        confirmation="SAVE PAPER REVIEW",
        values={"broker_restrictions": True, "recovery_drill": True},
        observed_at=now,
    )
    assert ledger.get_state("readiness_broker_restrictions") == "true"
    assert ledger.get_state("readiness_user_acceptance") == "false"
