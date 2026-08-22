from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from options_bot.cli import parser
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive
from options_bot.ml_model import SignalQualityModel, save as save_model
from options_bot.research_ledger import UsageRole, record_usage
from options_bot.upstox_data import UpstoxCandle
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

IST = ZoneInfo("Asia/Kolkata")


def _seed_archive(tmp_path: Path) -> Path:
    archive_path = tmp_path / "market.sqlite3"
    archive = MarketArchive(archive_path)
    archive.initialize()
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    underlying = [
        UpstoxCandle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(4)
    ]
    archive.save_upstox_candles(
        underlying, token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE", collected_at=start,
    )
    archive.save_instruments(
        [Instrument("NIFTY13AUG2626600CE", "NSE_FO|1|13-08-2026", "NFO", "NIFTY",
                     "CE", 75, date(2026, 8, 13), 100)],
        start,
    )
    return archive_path


def _dispatch(args_list: list[str]):
    from options_bot.backtest_cli import dispatch_backtest

    args = parser().parse_args(args_list)
    return dispatch_backtest(args)


def _capture_dispatch(capsys, args_list: list[str]):
    exit_code = _dispatch(args_list)
    return exit_code, json.loads(capsys.readouterr().out)


def test_check_range_allows_development_and_denies_reused_test(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="Baseline", role=UsageRole.SCREENING,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    dev_code, dev_out = _capture_dispatch(
        capsys,
        ["backtest", "check-range", "--archive", str(archive_path), "--candidate", "X",
         "--role", "development", "--start", "2026-01-01", "--end", "2026-07-31"],
    )
    test_code, test_out = _capture_dispatch(
        capsys,
        ["backtest", "check-range", "--archive", str(archive_path), "--candidate", "X",
         "--role", "test", "--start", "2026-06-01", "--end", "2026-07-31"],
    )

    assert dev_code == 0 and dev_out["allowed"] is True
    assert test_code == 1 and test_out["allowed"] is False


def test_run_records_usage_and_returns_a_result(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "run", "--archive", str(archive_path), "--candidate", "Smoke",
         "--role", "development", "--start", "2026-08-01", "--end", "2026-08-07",
         "--params-json", json.dumps({"name": "Smoke"})],
    )

    assert exit_code == 0
    assert "status" in payload and "trades" in payload

    archive = MarketArchive(archive_path)
    with archive.connect() as con:
        rows = con.execute(
            "SELECT candidate_name, role FROM range_usage WHERE candidate_name='Smoke'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "development"


def test_run_refuses_a_blocked_test_range_without_touching_the_backtest_engine(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    archive_path = _seed_archive(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="Prior", role=UsageRole.SCREENING,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    called = {"hit": False}

    def _fail_if_called(*args, **kwargs):
        called["hit"] = True
        raise AssertionError("run_upstox_backtest must not be called when the range is blocked")

    import options_bot.backtest_cli as backtest_cli_module

    monkeypatch.setattr(backtest_cli_module, "run_upstox_backtest", _fail_if_called)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "run", "--archive", str(archive_path), "--candidate", "Blocked",
         "--role", "test", "--start", "2026-06-01", "--end", "2026-07-31",
         "--params-json", json.dumps({"name": "Blocked"})],
    )

    assert called["hit"] is False
    assert exit_code == 1
    assert payload["error"] == "blocked"

    with archive.connect() as con:
        rows = con.execute(
            "SELECT * FROM range_usage WHERE candidate_name='Blocked'"
        ).fetchall()
    assert rows == [], "a blocked test must never be recorded as if it ran"


def test_run_with_force_override_records_but_never_as_confirmed(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    archive_path = _seed_archive(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="Prior", role=UsageRole.SCREENING,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "run", "--archive", str(archive_path), "--candidate", "Forced",
         "--role", "test", "--start", "2026-06-01", "--end", "2026-07-31",
         "--params-json", json.dumps({"name": "Forced"}),
         "--force-override-reason", "testing the override path"],
    )

    assert exit_code == 0
    with archive.connect() as con:
        row = con.execute(
            "SELECT forced_override_reason, outcome_label FROM range_usage WHERE candidate_name='Forced'"
        ).fetchone()
    assert row[0] == "testing the override path"
    assert row[1] != "confirmed"


def test_validate_split_defers_when_test_range_is_blocked_but_still_runs_dev_and_validation(
    tmp_path: Path, capsys
) -> None:
    archive_path = _seed_archive(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="Prior", role=UsageRole.SCREENING,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "validate-split", "--archive", str(archive_path), "--candidate", "Deferred",
         "--params-json", json.dumps({"name": "Deferred"}),
         "--dev-start", "2026-01-01", "--dev-end", "2026-03-31",
         "--val-start", "2026-04-01", "--val-end", "2026-05-31",
         "--test-start", "2026-06-01", "--test-end", "2026-07-31"],
    )

    assert exit_code == 0
    assert payload["status"] == "deferred_no_test"
    assert "development" in payload and "validation" in payload
    assert "test" not in payload

    with archive.connect() as con:
        roles = {
            row[0]
            for row in con.execute(
                "SELECT role FROM range_usage WHERE candidate_name='Deferred'"
            ).fetchall()
        }
    assert roles == {"development", "validation"}


def test_validate_split_rejects_non_chronological_ranges(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "validate-split", "--archive", str(archive_path), "--candidate", "Bad",
         "--params-json", json.dumps({"name": "Bad"}),
         "--dev-start", "2026-04-01", "--dev-end", "2026-05-31",
         "--val-start", "2026-01-01", "--val-end", "2026-03-31",
         "--test-start", "2026-06-01", "--test-end", "2026-07-31"],
    )

    assert exit_code == 1
    assert payload["error"] == "invalid_ranges"


def test_ledger_redact_flag_omits_numeric_and_full_mode_includes_notes(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="X", role=UsageRole.DEVELOPMENT,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 3, 31),
        notes="49 trades, net 7697.05",
    )

    redacted_code, redacted = _capture_dispatch(
        capsys, ["backtest", "ledger", "--archive", str(archive_path), "--redact"]
    )
    full_code, full = _capture_dispatch(
        capsys, ["backtest", "ledger", "--archive", str(archive_path)]
    )

    assert redacted_code == 0 and full_code == 0
    assert "notes" not in redacted["usage_history"][0]
    assert full["usage_history"][0]["notes"] == "49 trades, net 7697.05"


def test_ledger_export_json_writes_file(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="X", role=UsageRole.DEVELOPMENT,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 3, 31),
    )
    export_target = tmp_path / "research" / "range_usage_ledger.json"

    exit_code, _ = _capture_dispatch(
        capsys,
        ["backtest", "ledger", "--archive", str(archive_path), "--export-json", str(export_target)],
    )

    assert exit_code == 0
    assert export_target.exists()
    assert "X" in export_target.read_text(encoding="utf-8")


def test_ledger_export_json_redact_writes_a_redacted_file(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="X", role=UsageRole.DEVELOPMENT,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 3, 31),
        notes="49 trades, net 7697.05",
    )
    export_target = tmp_path / "research" / "redacted-ledger.json"

    exit_code, _ = _capture_dispatch(
        capsys,
        ["backtest", "ledger", "--archive", str(archive_path),
         "--export-json", str(export_target), "--redact"],
    )

    assert exit_code == 0
    contents = export_target.read_text(encoding="utf-8")
    assert "7697.05" not in contents
    assert '"notes"' not in contents


def test_validate_split_without_test_args_is_dev_validation_only(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "validate-split", "--archive", str(archive_path), "--candidate", "DevOnly",
         "--params-json", json.dumps({"name": "DevOnly"}),
         "--dev-start", "2026-01-01", "--dev-end", "2026-03-31",
         "--val-start", "2026-04-01", "--val-end", "2026-05-31"],
    )

    assert exit_code == 0
    assert payload["status"] == "dev_validation_only"
    assert "test" not in payload

    archive = MarketArchive(archive_path)
    with archive.connect() as con:
        roles = {
            row[0]
            for row in con.execute(
                "SELECT role FROM range_usage WHERE candidate_name='DevOnly'"
            ).fetchall()
        }
    assert roles == {"development", "validation"}


def test_run_rejects_a_candidate_reused_with_different_parameters(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    _capture_dispatch(
        capsys,
        ["backtest", "run", "--archive", str(archive_path), "--candidate", "Reused",
         "--role", "development", "--start", "2026-01-01", "--end", "2026-03-31",
         "--params-json", json.dumps({"name": "Reused", "bullish_rsi_min": 60})],
    )

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "run", "--archive", str(archive_path), "--candidate", "Reused",
         "--role", "development", "--start", "2026-04-01", "--end", "2026-05-31",
         "--params-json", json.dumps({"name": "Reused", "bullish_rsi_min": 70})],
    )

    assert exit_code == 1
    assert payload["error"] == "params_mismatch"


def test_run_test_role_with_no_data_reports_unavailable_and_records_nothing(
    tmp_path: Path, capsys
) -> None:
    archive_path = _seed_archive(tmp_path)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "run", "--archive", str(archive_path), "--candidate", "NoData",
         "--role", "test", "--start", "2026-09-01", "--end", "2026-09-10",
         "--params-json", json.dumps({"name": "NoData"})],
    )

    assert exit_code == 1
    assert payload["error"] == "test_data_unavailable"

    archive = MarketArchive(archive_path)
    with archive.connect() as con:
        rows = con.execute(
            "SELECT * FROM range_usage WHERE candidate_name='NoData'"
        ).fetchall()
    assert rows == [], "a test attempt must not be spent when there was never any data to test against"


def test_validate_split_test_leg_with_no_data_does_not_spend_the_test_attempt(
    tmp_path: Path, capsys
) -> None:
    archive_path = _seed_archive(tmp_path)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "validate-split", "--archive", str(archive_path), "--candidate", "NoDataSplit",
         "--params-json", json.dumps({"name": "NoDataSplit"}),
         "--dev-start", "2026-01-01", "--dev-end", "2026-03-31",
         "--val-start", "2026-04-01", "--val-end", "2026-05-31",
         "--test-start", "2026-09-01", "--test-end", "2026-09-10"],
    )

    assert exit_code == 0
    assert payload["status"] == "test_data_unavailable"
    assert "test" not in payload

    archive = MarketArchive(archive_path)
    with archive.connect() as con:
        roles = {
            row[0]
            for row in con.execute(
                "SELECT role FROM range_usage WHERE candidate_name='NoDataSplit'"
            ).fetchall()
        }
    assert roles == {"development", "validation"}, "no test row should be recorded when data is unavailable"


def test_outcome_label_confirmed_is_not_a_valid_cli_choice() -> None:
    import pytest

    with pytest.raises(SystemExit):
        parser().parse_args(
            ["backtest", "run", "--archive", "x.sqlite3", "--candidate", "X",
             "--role", "test", "--start", "2026-01-01", "--end", "2026-01-31",
             "--params-json", "{}", "--outcome-label", "confirmed"]
        )


def _seed_model(tmp_path: Path) -> Path:
    model = SignalQualityModel(
        feature_names=(), means=(), stds=(), weights=(), bias=10.0, threshold=0.5, metadata={},
    )
    return save_model(model, tmp_path / "model.json")


def test_ml_validate_split_without_test_args_is_dev_validation_only(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    model_path = _seed_model(tmp_path)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "ml-validate-split", "--archive", str(archive_path), "--candidate", "MlDevOnly",
         "--model-path", str(model_path),
         "--base-params-json", json.dumps({"name": "MlDevOnly", "stop_risk_fraction": 1.6}),
         "--dev-start", "2026-01-01", "--dev-end", "2026-03-31",
         "--val-start", "2026-04-01", "--val-end", "2026-05-31"],
    )

    assert exit_code == 0
    assert payload["status"] == "dev_validation_only"
    assert "test" not in payload

    archive = MarketArchive(archive_path)
    with archive.connect() as con:
        roles = {
            row[0]
            for row in con.execute(
                "SELECT role FROM range_usage WHERE candidate_name='MlDevOnly'"
            ).fetchall()
        }
    assert roles == {"development", "validation"}


def test_ml_validate_split_defers_when_test_range_is_blocked(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    model_path = _seed_model(tmp_path)
    archive = MarketArchive(archive_path)
    record_usage(
        archive, candidate_name="Prior", role=UsageRole.SCREENING,
        underlying_key=NIFTY_UNDERLYING_KEY, timeframe="FIVE_MINUTE",
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "ml-validate-split", "--archive", str(archive_path), "--candidate", "MlDeferred",
         "--model-path", str(model_path),
         "--base-params-json", json.dumps({"name": "MlDeferred", "stop_risk_fraction": 1.6}),
         "--dev-start", "2026-01-01", "--dev-end", "2026-03-31",
         "--val-start", "2026-04-01", "--val-end", "2026-05-31",
         "--test-start", "2026-06-01", "--test-end", "2026-07-31"],
    )

    assert exit_code == 0
    assert payload["status"] == "deferred_no_test"
    assert "development" in payload and "validation" in payload
    assert "test" not in payload


def test_ml_validate_split_rejects_base_params_with_filter_fields(tmp_path: Path, capsys) -> None:
    archive_path = _seed_archive(tmp_path)
    model_path = _seed_model(tmp_path)

    exit_code, payload = _capture_dispatch(
        capsys,
        ["backtest", "ml-validate-split", "--archive", str(archive_path), "--candidate", "MlBadParams",
         "--model-path", str(model_path),
         "--base-params-json", json.dumps({"name": "MlBadParams", "bullish_rsi_min": 55}),
         "--dev-start", "2026-01-01", "--dev-end", "2026-03-31",
         "--val-start", "2026-04-01", "--val-end", "2026-05-31"],
    )

    assert exit_code == 1
    assert payload["error"] == "invalid_base_params"

    archive = MarketArchive(archive_path)
    with archive.connect() as con:
        rows = con.execute(
            "SELECT * FROM range_usage WHERE candidate_name='MlBadParams'"
        ).fetchall()
    assert rows == [], "an invalid base-params request must not touch the ledger at all"


def test_settings_for_archive_honors_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    from options_bot.backtest_cli import _settings_for_archive

    monkeypatch.setenv("PAPER_FEE_PER_ORDER", "42")
    archive_path = _seed_archive(tmp_path)

    settings = _settings_for_archive(str(archive_path))

    assert settings.paper_fee_per_order == 42
    assert settings.data_dir == archive_path.parent
