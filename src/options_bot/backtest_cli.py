"""Non-interactive `options-bot backtest ...` CLI.

Exists so an automated research pipeline can run Upstox historical
backtests from argv, without a human at the FastAPI dashboard. Every
`run`/`validate-split` invocation checks the range-usage ledger
(`research_ledger.py`) before touching the backtest engine and records
the usage immediately after, as one atomic CLI call -- there is no
window where a backtest can run without the ledger knowing about it.

Deliberately does not modify `upstox_backtest.py` or `validation.py`;
all ledger awareness lives here only, keeping the already-tested
backtest engine isolated exactly as this project's existing read-only
historical-replay discipline requires.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, time
from pathlib import Path

from .backtest import BacktestParameters, BacktestResult
from .config import Settings
from .market_archive import MarketArchive
from .research_ledger import (
    VALID_OUTCOME_LABELS,
    UsageRole,
    check_range,
    classify_confirmation,
    export_ledger_json,
    record_usage,
    research_context_for_evaluation,
    research_context_for_ideation,
)
from .upstox_backtest import run_upstox_backtest
from .upstox_ingest import NIFTY_UNDERLYING_KEY


def _parse_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


def _params_from_json(raw: str) -> BacktestParameters:
    data = json.loads(raw)
    allowed_weekdays = data.get("allowed_weekdays")
    return BacktestParameters(
        name=data.get("name", "Candidate"),
        bullish_rsi_min=data.get("bullish_rsi_min"),
        bearish_rsi_max=data.get("bearish_rsi_max"),
        minimum_atr=data.get("minimum_atr"),
        entry_start=_parse_time(data.get("entry_start")),
        entry_end=_parse_time(data.get("entry_end")),
        exclude_expiry_day=data.get("exclude_expiry_day", False),
        stop_risk_fraction=data.get("stop_risk_fraction", 0.8),
        maximum_hold_minutes=data.get("maximum_hold_minutes"),
        target_return=data.get("target_return"),
        trailing_stop=data.get("trailing_stop"),
        allowed_weekdays=tuple(allowed_weekdays) if allowed_weekdays else None,
    )


def _result_to_dict(result: BacktestResult) -> dict:
    return {
        "status": result.status,
        "trades": result.trades,
        "winners": result.winners,
        "losers": result.losers,
        "win_rate": result.win_rate,
        "net_pnl": result.net_pnl,
        "fees_paid": result.fees_paid,
        "max_drawdown": result.max_drawdown,
        "profit_factor": result.profit_factor,
        "reason": result.reason,
        "trading_days": result.trading_days,
        "data_gaps": result.data_gaps,
        "capital_deployed_total": result.capital_deployed_total,
        "capital_deployed_average": result.capital_deployed_average,
        "return_on_capital_pct": result.return_on_capital_pct,
    }


def _settings_for_archive(archive_path: str) -> Settings:
    data_dir = Path(archive_path).parent
    return Settings.from_env(
        {"DATA_DIR": str(data_dir), "DATABASE_PATH": str(data_dir / "paper.sqlite3")}
    )


def _write_json_out(path: str | None, payload: dict) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def register_backtest_parser(commands: argparse._SubParsersAction) -> None:
    backtest = commands.add_parser("backtest")
    backtest_commands = backtest.add_subparsers(dest="backtest_command", required=True)

    def _common(sub: argparse.ArgumentParser, *, needs_role: bool = True) -> None:
        sub.add_argument("--archive", required=True, help="Path to the MarketArchive sqlite3 file")
        sub.add_argument("--candidate", required=True)
        if needs_role:
            sub.add_argument("--role", required=True, choices=[r.value for r in UsageRole])
        sub.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
        sub.add_argument("--timeframe", default="FIVE_MINUTE")

    check = backtest_commands.add_parser("check-range")
    _common(check)
    check.add_argument("--start", required=True)
    check.add_argument("--end", required=True)

    run = backtest_commands.add_parser("run")
    _common(run)
    run.add_argument("--start", required=True)
    run.add_argument("--end", required=True)
    run.add_argument("--params-json", required=True)
    run.add_argument("--force-override-reason")
    run.add_argument("--outcome-label", choices=sorted(VALID_OUTCOME_LABELS))
    run.add_argument("--json-out")

    split = backtest_commands.add_parser("validate-split")
    _common(split, needs_role=False)
    split.add_argument("--params-json", required=True)
    split.add_argument("--dev-start", required=True)
    split.add_argument("--dev-end", required=True)
    split.add_argument("--val-start", required=True)
    split.add_argument("--val-end", required=True)
    split.add_argument("--test-start", required=True)
    split.add_argument("--test-end", required=True)
    split.add_argument("--force-override-reason")
    split.add_argument("--outcome-label", choices=sorted(VALID_OUTCOME_LABELS))
    split.add_argument("--json-out")

    ledger = backtest_commands.add_parser("ledger")
    ledger.add_argument("--archive", required=True)
    ledger.add_argument("--export-json")
    ledger.add_argument("--redact", action="store_true")


def dispatch_backtest(args: argparse.Namespace) -> int:
    command = args.backtest_command
    if command == "check-range":
        return _do_check_range(args)
    if command == "run":
        return _do_run(args)
    if command == "validate-split":
        return _do_validate_split(args)
    if command == "ledger":
        return _do_ledger(args)
    print(f"ERROR: unknown backtest command {command!r}")
    return 2


def _do_check_range(args: argparse.Namespace) -> int:
    archive = MarketArchive(args.archive)
    archive.initialize()
    result = check_range(
        archive,
        candidate_name=args.candidate,
        role=UsageRole(args.role),
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )
    print(json.dumps({"allowed": result.allowed, "reason": result.reason}, indent=2))
    return 0 if result.allowed else 1


def _do_run(args: argparse.Namespace) -> int:
    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    role = UsageRole(args.role)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    check = check_range(
        archive,
        candidate_name=args.candidate,
        role=role,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=start,
        end=end,
    )
    if not check.allowed and not args.force_override_reason:
        print(json.dumps({"error": "blocked", "reason": check.reason}, indent=2))
        return 1
    parameters = _params_from_json(args.params_json)
    result = run_upstox_backtest(
        archive,
        start=start,
        end=end,
        settings=settings,
        parameters=parameters,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
    )
    try:
        record_usage(
            archive,
            candidate_name=args.candidate,
            role=role,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
            start=start,
            end=end,
            outcome_label=args.outcome_label,
            forced_override_reason=args.force_override_reason if not check.allowed else None,
        )
    except ValueError as exc:
        print(json.dumps({"error": "invalid_record", "reason": str(exc)}, indent=2))
        return 1
    payload = _result_to_dict(result)
    print(json.dumps(payload, indent=2))
    _write_json_out(args.json_out, payload)
    return 0


def _do_validate_split(args: argparse.Namespace) -> int:
    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    parameters = _params_from_json(args.params_json)
    dev_start, dev_end = date.fromisoformat(args.dev_start), date.fromisoformat(args.dev_end)
    val_start, val_end = date.fromisoformat(args.val_start), date.fromisoformat(args.val_end)
    test_start, test_end = date.fromisoformat(args.test_start), date.fromisoformat(args.test_end)
    if not (dev_start <= dev_end < val_start <= val_end < test_start <= test_end):
        print(
            json.dumps(
                {
                    "error": "invalid_ranges",
                    "reason": "development, validation, and test ranges must be "
                    "chronological and non-overlapping",
                },
                indent=2,
            )
        )
        return 1

    def _run(start: date, end: date) -> BacktestResult:
        return run_upstox_backtest(
            archive,
            start=start,
            end=end,
            settings=settings,
            parameters=parameters,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
        )

    test_check = check_range(
        archive,
        candidate_name=args.candidate,
        role=UsageRole.TEST,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=test_start,
        end=test_end,
    )

    development = _run(dev_start, dev_end)
    validation = _run(val_start, val_end)
    record_usage(
        archive, candidate_name=args.candidate, role=UsageRole.DEVELOPMENT,
        underlying_key=args.underlying_key, timeframe=args.timeframe, start=dev_start, end=dev_end,
    )
    record_usage(
        archive, candidate_name=args.candidate, role=UsageRole.VALIDATION,
        underlying_key=args.underlying_key, timeframe=args.timeframe, start=val_start, end=val_end,
    )

    if not test_check.allowed and not args.force_override_reason:
        payload = {
            "status": "deferred_no_test",
            "reason": test_check.reason,
            "development": _result_to_dict(development),
            "validation": _result_to_dict(validation),
        }
        print(json.dumps(payload, indent=2))
        _write_json_out(args.json_out, payload)
        return 0

    test = _run(test_start, test_end)
    try:
        record_usage(
            archive,
            candidate_name=args.candidate,
            role=UsageRole.TEST,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
            start=test_start,
            end=test_end,
            outcome_label=args.outcome_label,
            forced_override_reason=args.force_override_reason if not test_check.allowed else None,
        )
    except ValueError as exc:
        print(json.dumps({"error": "invalid_record", "reason": str(exc)}, indent=2))
        return 1

    classification = classify_confirmation(
        archive,
        candidate_name=args.candidate,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=test_start,
        end=test_end,
    )
    payload = {
        "status": "completed",
        "classification": classification,
        "development": _result_to_dict(development),
        "validation": _result_to_dict(validation),
        "test": _result_to_dict(test),
    }
    print(json.dumps(payload, indent=2))
    _write_json_out(args.json_out, payload)
    return 0


def _do_ledger(args: argparse.Namespace) -> int:
    archive = MarketArchive(args.archive)
    archive.initialize()
    context = (
        research_context_for_ideation(archive) if args.redact else research_context_for_evaluation(archive)
    )
    if args.export_json:
        export_ledger_json(archive, args.export_json)
    print(json.dumps(context, indent=2, sort_keys=True))
    return 0
