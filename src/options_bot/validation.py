"""Offline strategy comparison with explicit development/validation/test splits."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

from .backtest import BacktestParameters, BacktestResult, run_momentum_backtest
from .config import Settings
from .market_archive import MarketArchive


@dataclass(frozen=True)
class ValidationRow:
    name: str
    parameters: BacktestParameters
    development: BacktestResult
    validation: BacktestResult
    test: BacktestResult | None = None


@dataclass(frozen=True)
class ValidationReport:
    status: str
    selected_name: str
    rows: tuple[ValidationRow, ...]
    warning: str
    development_range: tuple[date, date]
    validation_range: tuple[date, date]
    test_range: tuple[date, date]


STRATEGY_VARIANTS = (
    BacktestParameters(name="Baseline"),
    BacktestParameters(name="Strict RSI", bullish_rsi_min=55, bearish_rsi_max=45),
    BacktestParameters(name="ATR floor", minimum_atr=15),
    BacktestParameters(name="Morning entries", entry_start=time(9, 30), entry_end=time(12)),
    BacktestParameters(name="Tuesday–Thursday", allowed_weekdays=(1, 2, 3)),
    BacktestParameters(name="No expiry day", exclude_expiry_day=True),
    BacktestParameters(name="Tighter stop", stop_risk_fraction=0.6),
    BacktestParameters(name="30-minute hold", maximum_hold_minutes=30),
    BacktestParameters(name="20% target", target_return=0.2),
    BacktestParameters(name="10% trailing stop", trailing_stop=0.1),
)


def run_strategy_validation(
    archive: MarketArchive,
    settings: Settings,
    *,
    development_start: date,
    development_end: date,
    validation_start: date,
    validation_end: date,
    test_start: date,
    test_end: date,
) -> ValidationReport:
    if not (
        development_start <= development_end < validation_start
        <= validation_end < test_start <= test_end
    ):
        raise ValueError(
            "Use non-overlapping chronological development, validation, and test ranges"
        )
    rows: list[ValidationRow] = []
    for variant in STRATEGY_VARIANTS:
        development = run_momentum_backtest(
            archive,
            development_start,
            development_end,
            settings,
            variant,
        )
        validation = run_momentum_backtest(
            archive,
            validation_start,
            validation_end,
            settings,
            variant,
        )
        rows.append(ValidationRow(variant.name, variant, development, validation))
    eligible = [row for row in rows if row.validation.trades > 0]
    selected = max(
        eligible or rows,
        key=lambda row: (
            row.validation.net_pnl - row.validation.max_drawdown,
            row.validation.trades,
        ),
    )
    test_result = run_momentum_backtest(
        archive,
        test_start,
        test_end,
        settings,
        selected.parameters,
    )
    rows = [
        ValidationRow(
            row.name,
            row.parameters,
            row.development,
            row.validation,
            test_result if row.name == selected.name else None,
        )
        for row in rows
    ]
    gaps = max(
        (result.data_gaps for row in rows for result in (row.development, row.validation)),
        default=0,
    )
    enough = bool(eligible) and test_result.trades > 0
    warning = (
        "Archive gaps are present; comparisons are exploratory only."
        if gaps
        else (
            "Selected once from validation data and evaluated once on the untouched test range."
            if enough
            else "Insufficient matching option history in one or more ranges."
        )
    )
    return ValidationReport(
        "READY" if enough and not gaps else "PRELIMINARY",
        selected.name,
        tuple(rows),
        warning,
        (development_start, development_end),
        (validation_start, validation_end),
        (test_start, test_end),
    )


def export_validation_csv(report: ValidationReport, target: str | Path) -> Path:
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "variant",
                "selected",
                "development_trades",
                "development_net_pnl",
                "validation_trades",
                "validation_net_pnl",
                "validation_drawdown",
                "test_trades",
                "test_net_pnl",
                "test_drawdown",
            )
        )
        for row in report.rows:
            writer.writerow(
                (
                    row.name,
                    row.name == report.selected_name,
                    row.development.trades,
                    row.development.net_pnl,
                    row.validation.trades,
                    row.validation.net_pnl,
                    row.validation.max_drawdown,
                    row.test.trades if row.test else "",
                    row.test.net_pnl if row.test else "",
                    row.test.max_drawdown if row.test else "",
                )
            )
    return destination
