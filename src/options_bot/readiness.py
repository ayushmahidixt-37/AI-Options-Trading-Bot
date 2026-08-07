"""Evidence-based paper-readiness review; never enables live execution."""

from __future__ import annotations

import csv
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
from .connections import ConnectionSnapshot
from .ledger import PaperLedger
from .market_archive import MarketArchive


MANUAL_REVIEW_FIELDS = {
    "broker_restrictions": "Broker/API restrictions reviewed",
    "recovery_drill": "Tablet restart and recovery drill completed",
    "user_acceptance": "Operator accepts paper results and limitations",
}


@dataclass(frozen=True)
class ReadinessCriterion:
    key: str
    group: str
    label: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    generated_at: datetime
    status: str
    passed: int
    total: int
    criteria: tuple[ReadinessCriterion, ...]
    manual_review: dict[str, bool]
    closed_trades: int
    trading_days: int


def save_manual_review(
    ledger: PaperLedger,
    *,
    confirmation: str,
    values: dict[str, bool],
    observed_at: datetime,
) -> None:
    if confirmation != "SAVE PAPER REVIEW":
        raise ValueError("Type SAVE PAPER REVIEW exactly")
    for key in MANUAL_REVIEW_FIELDS:
        ledger.set_state(f"readiness_{key}", "true" if values.get(key) else "false")
    ledger.record_event(
        observed_at.isoformat(),
        "INFO",
        "readiness_review",
        "Manual paper-readiness acknowledgements updated; live trading remains disabled",
    )


def build_readiness_report(
    settings: Settings,
    ledger: PaperLedger,
    archive: MarketArchive,
    connection: ConnectionSnapshot,
    *,
    web_password: str,
    observed_at: datetime | None = None,
) -> ReadinessReport:
    now = (observed_at or datetime.now(settings.timezone)).astimezone(settings.timezone)
    archive_stats = archive.stats()
    metrics = archive.readiness_metrics()
    performance = ledger.performance_summary()
    closed_trades = int(performance["trades"])
    open_positions = len(ledger.open_positions())
    heartbeat_fresh = bool(
        connection.monitor_last_run_at
        and now - connection.monitor_last_run_at <= timedelta(minutes=2)
    )
    backup_dir = settings.data_dir / "backups"
    backup_exists = any(backup_dir.glob("market-data-*.sqlite3"))
    credentials_secure = _credentials_are_private(settings.credentials_path)
    no_overdue_positions = now.time() < settings.force_exit or open_positions == 0
    drawdown_limit = settings.starting_capital * 0.10
    criteria = (
        ReadinessCriterion(
            "archive_days",
            "Data quality",
            "At least 20 archived NIFTY sessions",
            metrics.trading_days >= 20,
            f"{metrics.trading_days} session(s) archived",
        ),
        ReadinessCriterion(
            "archive_gaps",
            "Data quality",
            "No detected five-minute NIFTY gaps",
            archive_stats.missing_five_minute_buckets == 0,
            f"{archive_stats.missing_five_minute_buckets} possible missing bucket(s)",
        ),
        ReadinessCriterion(
            "archive_integrity",
            "Data quality",
            "SQLite archive integrity passes",
            archive_stats.integrity_status == "ok",
            f"Integrity: {archive_stats.integrity_status}",
        ),
        ReadinessCriterion(
            "paper_trades",
            "Paper evidence",
            "At least 100 closed paper trades",
            closed_trades >= 100,
            f"{closed_trades} closed paper trade(s)",
        ),
        ReadinessCriterion(
            "drawdown",
            "Paper evidence",
            "All-time drawdown remains within 10% of paper capital",
            float(performance["max_drawdown"]) <= drawdown_limit,
            f"Drawdown {float(performance['max_drawdown']):.2f}; limit {drawdown_limit:.2f}",
        ),
        ReadinessCriterion(
            "force_exit",
            "Risk and recovery",
            "No paper position remains after force-exit time",
            no_overdue_positions,
            f"{open_positions} open position(s)",
        ),
        ReadinessCriterion(
            "heartbeat",
            "Reliability",
            "Monitor heartbeat is current",
            heartbeat_fresh,
            (
                f"Last heartbeat {connection.monitor_last_run_at.isoformat()}"
                if connection.monitor_last_run_at
                else "No monitor heartbeat recorded"
            ),
        ),
        ReadinessCriterion(
            "failures",
            "Reliability",
            "No active consecutive monitor failures",
            connection.consecutive_failures == 0,
            f"{connection.consecutive_failures} consecutive failure(s)",
        ),
        ReadinessCriterion(
            "backup",
            "Operational recovery",
            "At least one local archive backup exists",
            backup_exists,
            "Backup found" if backup_exists else "No archive backup found",
        ),
        ReadinessCriterion(
            "credentials",
            "Security",
            "Credentials file has private permissions",
            credentials_secure,
            "Private permissions verified" if credentials_secure else "Use chmod 600 credentials.env",
        ),
        ReadinessCriterion(
            "password",
            "Security",
            "Default dashboard password has been changed",
            web_password != "12345",
            "Custom password configured" if web_password != "12345" else "Default 12345 is still active",
        ),
        ReadinessCriterion(
            "paper_boundary",
            "Safety boundary",
            "Paper-only configuration remains enforced",
            (
                settings.trading_mode == "paper"
                and not settings.live_trading_enabled
                and not settings.auto_start
            ),
            "Live execution remains unreachable",
        ),
    )
    manual = {
        key: ledger.get_state(f"readiness_{key}", "false") == "true"
        for key in MANUAL_REVIEW_FIELDS
    }
    manual_criteria = tuple(
        ReadinessCriterion(
            key,
            "Manual review",
            label,
            manual[key],
            "Acknowledged" if manual[key] else "Not yet acknowledged",
        )
        for key, label in MANUAL_REVIEW_FIELDS.items()
    )
    all_criteria = criteria + manual_criteria
    passed = sum(item.passed for item in all_criteria)
    complete = passed == len(all_criteria)
    return ReadinessReport(
        now,
        "PAPER REVIEW COMPLETE" if complete else "BLOCKED — MORE PAPER EVIDENCE REQUIRED",
        passed,
        len(all_criteria),
        all_criteria,
        manual,
        closed_trades,
        metrics.trading_days,
    )


def export_readiness_csv(report: ReadinessReport, target: str | Path) -> Path:
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("generated_at", report.generated_at.isoformat()))
        writer.writerow(("status", report.status))
        writer.writerow(("live_trading_approved", "false"))
        writer.writerow(())
        writer.writerow(("group", "criterion", "passed", "detail"))
        for item in report.criteria:
            writer.writerow((item.group, item.label, item.passed, item.detail))
    return destination


def _credentials_are_private(path: Path) -> bool:
    if not path.is_file():
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    return mode & 0o077 == 0
