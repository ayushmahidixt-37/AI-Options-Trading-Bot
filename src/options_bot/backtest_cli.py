"""Non-interactive `options-bot backtest ...` CLI.

Exists so an automated research pipeline can run Upstox historical
backtests from argv, without a human at the FastAPI dashboard. Every
`run`/`validate-split` invocation checks the range-usage ledger
(`research_ledger.py`) before touching the backtest engine and records
the usage immediately after -- for the scarce `test` role, the check and
the reservation happen atomically (`reserve_test_range`) so a crash or a
second concurrent caller can't both observe "allowed".

Deliberately does not modify `upstox_backtest.py` or `validation.py`;
all ledger awareness lives here only, keeping the already-tested
backtest engine isolated exactly as this project's existing read-only
historical-replay discipline requires.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, time
from pathlib import Path

from .backtest import BacktestParameters, BacktestResult
from .config import Settings
from .market_archive import MarketArchive
from . import ml_model as ml_model_module
from .research_ledger import (
    CALLER_ASSIGNABLE_OUTCOME_LABELS,
    UsageRole,
    check_range,
    classify_confirmation,
    compute_params_fingerprint,
    export_ledger_json,
    finalize_test_reservation,
    has_underlying_data,
    initialize_ledger,
    record_usage,
    reserve_test_range,
    research_context_for_evaluation,
    research_context_for_ideation,
    verify_params_fingerprint,
)
from .upstox_backtest import run_upstox_backtest
from .upstox_ml_backtest import run_upstox_ml_backtest
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
    """Real configured settings (environment, plus any `--config` file that
    `cli.py` already merged into ``os.environ`` before dispatch), only
    overriding the archive-derived paths. Building an isolated two-key
    mapping instead would silently revert every fee/slippage/risk/timezone
    assumption to `Settings` defaults, making CLI research results disagree
    with dashboard and production runs.
    """
    data_dir = Path(archive_path).parent
    overrides = dict(os.environ)
    overrides["DATA_DIR"] = str(data_dir)
    overrides.setdefault("DATABASE_PATH", str(data_dir / "paper.sqlite3"))
    return Settings.from_env(overrides)


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
    run.add_argument("--outcome-label", choices=sorted(CALLER_ASSIGNABLE_OUTCOME_LABELS))
    run.add_argument("--json-out")

    split = backtest_commands.add_parser("validate-split")
    _common(split, needs_role=False)
    split.add_argument("--params-json", required=True)
    split.add_argument("--dev-start", required=True)
    split.add_argument("--dev-end", required=True)
    split.add_argument("--val-start", required=True)
    split.add_argument("--val-end", required=True)
    split.add_argument(
        "--test-start",
        help="Omit together with --test-end for a development/validation-only cycle "
        "-- never invent a placeholder test range.",
    )
    split.add_argument("--test-end")
    split.add_argument("--force-override-reason")
    split.add_argument("--outcome-label", choices=sorted(CALLER_ASSIGNABLE_OUTCOME_LABELS))
    split.add_argument("--json-out")

    ledger = backtest_commands.add_parser("ledger")
    ledger.add_argument("--archive", required=True)
    ledger.add_argument("--export-json")
    ledger.add_argument("--redact", action="store_true")

    ml_split = backtest_commands.add_parser("ml-validate-split")
    _common(ml_split, needs_role=False)
    ml_split.add_argument("--model-path", required=True, help="Path to a SignalQualityModel JSON file")
    ml_split.add_argument(
        "--base-params-json",
        required=True,
        help="Exit-shell BacktestParameters JSON (stop/target/trailing/max-hold only -- "
        "any RSI/ATR/time/weekday filter fields are rejected, since the ML model replaces them)",
    )
    ml_split.add_argument("--dev-start", required=True)
    ml_split.add_argument("--dev-end", required=True)
    ml_split.add_argument("--val-start", required=True)
    ml_split.add_argument("--val-end", required=True)
    ml_split.add_argument(
        "--test-start",
        help="Omit together with --test-end for a development/validation-only cycle "
        "-- never invent a placeholder test range.",
    )
    ml_split.add_argument("--test-end")
    ml_split.add_argument("--force-override-reason")
    ml_split.add_argument("--outcome-label", choices=sorted(CALLER_ASSIGNABLE_OUTCOME_LABELS))
    ml_split.add_argument("--json-out")


def dispatch_backtest(args: argparse.Namespace) -> int:
    command = args.backtest_command
    if command == "check-range":
        return _do_check_range(args)
    if command == "run":
        return _do_run(args)
    if command == "validate-split":
        return _do_validate_split(args)
    if command == "ml-validate-split":
        return _do_ml_validate_split(args)
    if command == "ledger":
        return _do_ledger(args)
    print(f"ERROR: unknown backtest command {command!r}")
    return 2


def _do_check_range(args: argparse.Namespace) -> int:
    archive = MarketArchive(args.archive)
    archive.initialize()
    initialize_ledger(archive)
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
    initialize_ledger(archive)
    settings = _settings_for_archive(args.archive)
    role = UsageRole(args.role)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    fingerprint = compute_params_fingerprint(args.params_json)
    mismatch = verify_params_fingerprint(
        archive, candidate_name=args.candidate, params_fingerprint=fingerprint
    )
    if mismatch:
        print(json.dumps({"error": "params_mismatch", "reason": mismatch}, indent=2))
        return 1

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

    if role == UsageRole.TEST:
        if not args.force_override_reason and not has_underlying_data(
            archive, underlying_key=args.underlying_key, timeframe=args.timeframe, start=start, end=end
        ):
            print(
                json.dumps(
                    {
                        "error": "test_data_unavailable",
                        "reason": "no archived underlying candles in this range yet -- "
                        "not spending a test attempt",
                    },
                    indent=2,
                )
            )
            return 1
        reservation = reserve_test_range(
            archive,
            candidate_name=args.candidate,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
            start=start,
            end=end,
            forced_override_reason=args.force_override_reason,
            params_fingerprint=fingerprint,
        )
        if reservation is None:
            print(
                json.dumps(
                    {"error": "blocked", "reason": "test range failed the ledger eligibility re-check"},
                    indent=2,
                )
            )
            return 1
        result = run_upstox_backtest(
            archive,
            start=start,
            end=end,
            settings=settings,
            parameters=parameters,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
        )
        finalize_test_reservation(archive, reservation, outcome_label=args.outcome_label)
    else:
        result = run_upstox_backtest(
            archive,
            start=start,
            end=end,
            settings=settings,
            parameters=parameters,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
        )
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
            params_fingerprint=fingerprint,
        )

    payload = _result_to_dict(result)
    print(json.dumps(payload, indent=2))
    _write_json_out(args.json_out, payload)
    return 0


def _do_validate_split(args: argparse.Namespace) -> int:
    archive = MarketArchive(args.archive)
    archive.initialize()
    initialize_ledger(archive)
    settings = _settings_for_archive(args.archive)

    fingerprint = compute_params_fingerprint(args.params_json)
    mismatch = verify_params_fingerprint(
        archive, candidate_name=args.candidate, params_fingerprint=fingerprint
    )
    if mismatch:
        print(json.dumps({"error": "params_mismatch", "reason": mismatch}, indent=2))
        return 1

    parameters = _params_from_json(args.params_json)
    dev_start, dev_end = date.fromisoformat(args.dev_start), date.fromisoformat(args.dev_end)
    val_start, val_end = date.fromisoformat(args.val_start), date.fromisoformat(args.val_end)
    test_start = date.fromisoformat(args.test_start) if args.test_start else None
    test_end = date.fromisoformat(args.test_end) if args.test_end else None

    if (test_start is None) != (test_end is None):
        print(
            json.dumps(
                {
                    "error": "invalid_ranges",
                    "reason": "--test-start and --test-end must both be given, or both omitted "
                    "for a development/validation-only cycle",
                },
                indent=2,
            )
        )
        return 1

    chronological = dev_start <= dev_end < val_start <= val_end
    if test_start is not None:
        chronological = chronological and val_end < test_start <= test_end
    if not chronological:
        print(
            json.dumps(
                {
                    "error": "invalid_ranges",
                    "reason": "development, validation, and (if given) test ranges must be "
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

    test_check = (
        check_range(
            archive,
            candidate_name=args.candidate,
            role=UsageRole.TEST,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
            start=test_start,
            end=test_end,
        )
        if test_start is not None
        else None
    )

    development = _run(dev_start, dev_end)
    validation = _run(val_start, val_end)
    record_usage(
        archive,
        candidate_name=args.candidate,
        role=UsageRole.DEVELOPMENT,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=dev_start,
        end=dev_end,
        params_fingerprint=fingerprint,
    )
    record_usage(
        archive,
        candidate_name=args.candidate,
        role=UsageRole.VALIDATION,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=val_start,
        end=val_end,
        params_fingerprint=fingerprint,
    )

    if test_start is None:
        payload = {
            "status": "dev_validation_only",
            "development": _result_to_dict(development),
            "validation": _result_to_dict(validation),
        }
        print(json.dumps(payload, indent=2))
        _write_json_out(args.json_out, payload)
        return 0

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

    if not args.force_override_reason and not has_underlying_data(
        archive, underlying_key=args.underlying_key, timeframe=args.timeframe, start=test_start, end=test_end
    ):
        payload = {
            "status": "test_data_unavailable",
            "reason": "no archived underlying candles in the test range yet -- not spending a "
            "test attempt; retry once more data has been ingested",
            "development": _result_to_dict(development),
            "validation": _result_to_dict(validation),
        }
        print(json.dumps(payload, indent=2))
        _write_json_out(args.json_out, payload)
        return 0

    reservation = reserve_test_range(
        archive,
        candidate_name=args.candidate,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=test_start,
        end=test_end,
        forced_override_reason=args.force_override_reason,
        params_fingerprint=fingerprint,
    )
    if reservation is None:
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
    finalize_test_reservation(archive, reservation, outcome_label=args.outcome_label)

    classification = classify_confirmation(
        archive,
        candidate_name=args.candidate,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=test_start,
        end=test_end,
    )
    payload = {
        "status": "completed" if test.trades else "completed_zero_trades",
        "classification": classification,
        "development": _result_to_dict(development),
        "validation": _result_to_dict(validation),
        "test": _result_to_dict(test),
    }
    print(json.dumps(payload, indent=2))
    _write_json_out(args.json_out, payload)
    return 0


_ML_FILTER_FIELDS = (
    "bullish_rsi_min",
    "bearish_rsi_max",
    "minimum_atr",
    "entry_start",
    "entry_end",
    "exclude_expiry_day",
    "allowed_weekdays",
)


def _ml_combined_fingerprint_source(base_params_json: str, model_path: str) -> str:
    """Canonical JSON binding a candidate to both its exit-shell params and
    the exact trained model bytes -- so retraining a model under the same
    candidate name is caught by ``verify_params_fingerprint`` exactly like a
    changed ``BacktestParameters`` would be today."""
    model_bytes = Path(model_path).read_bytes()
    payload = {
        "base_params": json.loads(base_params_json),
        "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
    }
    return json.dumps(payload, sort_keys=True)


def _do_ml_validate_split(args: argparse.Namespace) -> int:
    archive = MarketArchive(args.archive)
    archive.initialize()
    initialize_ledger(archive)
    settings = _settings_for_archive(args.archive)

    base_params = _params_from_json(args.base_params_json)
    set_filter_fields = [
        field
        for field in _ML_FILTER_FIELDS
        if getattr(base_params, field) not in (None, False)
    ]
    if set_filter_fields:
        print(
            json.dumps(
                {
                    "error": "invalid_base_params",
                    "reason": "--base-params-json must be an unfiltered exit-shell (stop/target/"
                    "trailing/max-hold only) -- the ML model replaces RSI/ATR/time/weekday "
                    f"filtering; these fields must stay unset: {set_filter_fields!r}",
                },
                indent=2,
            )
        )
        return 1

    model = ml_model_module.load(args.model_path)
    fingerprint = compute_params_fingerprint(
        _ml_combined_fingerprint_source(args.base_params_json, args.model_path)
    )
    mismatch = verify_params_fingerprint(
        archive, candidate_name=args.candidate, params_fingerprint=fingerprint
    )
    if mismatch:
        print(json.dumps({"error": "params_mismatch", "reason": mismatch}, indent=2))
        return 1

    dev_start, dev_end = date.fromisoformat(args.dev_start), date.fromisoformat(args.dev_end)
    val_start, val_end = date.fromisoformat(args.val_start), date.fromisoformat(args.val_end)
    test_start = date.fromisoformat(args.test_start) if args.test_start else None
    test_end = date.fromisoformat(args.test_end) if args.test_end else None

    if (test_start is None) != (test_end is None):
        print(
            json.dumps(
                {
                    "error": "invalid_ranges",
                    "reason": "--test-start and --test-end must both be given, or both omitted "
                    "for a development/validation-only cycle",
                },
                indent=2,
            )
        )
        return 1

    chronological = dev_start <= dev_end < val_start <= val_end
    if test_start is not None:
        chronological = chronological and val_end < test_start <= test_end
    if not chronological:
        print(
            json.dumps(
                {
                    "error": "invalid_ranges",
                    "reason": "development, validation, and (if given) test ranges must be "
                    "chronological and non-overlapping",
                },
                indent=2,
            )
        )
        return 1

    def _run(start: date, end: date) -> BacktestResult:
        return run_upstox_ml_backtest(
            archive,
            model=model,
            start=start,
            end=end,
            settings=settings,
            parameters=base_params,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
        )

    test_check = (
        check_range(
            archive,
            candidate_name=args.candidate,
            role=UsageRole.TEST,
            underlying_key=args.underlying_key,
            timeframe=args.timeframe,
            start=test_start,
            end=test_end,
        )
        if test_start is not None
        else None
    )

    development = _run(dev_start, dev_end)
    validation = _run(val_start, val_end)
    record_usage(
        archive,
        candidate_name=args.candidate,
        role=UsageRole.DEVELOPMENT,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=dev_start,
        end=dev_end,
        params_fingerprint=fingerprint,
    )
    record_usage(
        archive,
        candidate_name=args.candidate,
        role=UsageRole.VALIDATION,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=val_start,
        end=val_end,
        params_fingerprint=fingerprint,
    )

    if test_start is None:
        payload = {
            "status": "dev_validation_only",
            "development": _result_to_dict(development),
            "validation": _result_to_dict(validation),
        }
        print(json.dumps(payload, indent=2))
        _write_json_out(args.json_out, payload)
        return 0

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

    if not args.force_override_reason and not has_underlying_data(
        archive, underlying_key=args.underlying_key, timeframe=args.timeframe, start=test_start, end=test_end
    ):
        payload = {
            "status": "test_data_unavailable",
            "reason": "no archived underlying candles in the test range yet -- not spending a "
            "test attempt; retry once more data has been ingested",
            "development": _result_to_dict(development),
            "validation": _result_to_dict(validation),
        }
        print(json.dumps(payload, indent=2))
        _write_json_out(args.json_out, payload)
        return 0

    reservation = reserve_test_range(
        archive,
        candidate_name=args.candidate,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=test_start,
        end=test_end,
        forced_override_reason=args.force_override_reason,
        params_fingerprint=fingerprint,
    )
    if reservation is None:
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
    finalize_test_reservation(archive, reservation, outcome_label=args.outcome_label)

    classification = classify_confirmation(
        archive,
        candidate_name=args.candidate,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=test_start,
        end=test_end,
    )
    payload = {
        "status": "completed" if test.trades else "completed_zero_trades",
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
    initialize_ledger(archive)
    context = (
        research_context_for_ideation(archive) if args.redact else research_context_for_evaluation(archive)
    )
    if args.export_json:
        export_ledger_json(archive, args.export_json, redact=args.redact)
    print(json.dumps(context, indent=2, sort_keys=True))
    return 0
