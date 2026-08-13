from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from options_bot.market_archive import MarketArchive
from options_bot.research_ledger import (
    RangeUsage,
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
    resolve_archive_date_range,
    verify_params_fingerprint,
)
from options_bot.upstox_data import UpstoxCandle

UNDERLYING = "NSE_INDEX|Nifty 50"
TIMEFRAME = "FIVE_MINUTE"
IST = ZoneInfo("Asia/Kolkata")


def archive(tmp_path: Path) -> MarketArchive:
    result = MarketArchive(tmp_path / "market-data.sqlite3")
    result.initialize()
    return result


def _seed_candles(store: MarketArchive, day: date) -> None:
    start = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    candles = [
        UpstoxCandle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(4)
    ]
    store.save_upstox_candles(
        candles, token=UNDERLYING, exchange="NSE_INDEX", timeframe=TIMEFRAME, collected_at=start
    )


def test_development_and_validation_ranges_may_always_be_reused(tmp_path: Path) -> None:
    store = archive(tmp_path)

    first = check_range(
        store, candidate_name="Baseline", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 3, 31),
    )
    record_usage(
        store, candidate_name="Baseline", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 3, 31),
    )
    second = check_range(
        store, candidate_name="Strict RSI", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 3, 31),
    )

    assert first.allowed is True
    assert second.allowed is True


def test_test_range_must_start_strictly_after_every_prior_range(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Baseline", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 3, 31),
    )

    overlapping = check_range(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 3, 1), end=date(2026, 4, 30),
    )
    exactly_on_boundary = check_range(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 3, 31), end=date(2026, 4, 30),
    )
    genuinely_fresh = check_range(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 4, 1), end=date(2026, 4, 30),
    )

    assert overlapping.allowed is False
    assert exactly_on_boundary.allowed is False, "start == latest range_end must not count as fresh"
    assert genuinely_fresh.allowed is True
    assert len(overlapping.conflicting_rows) == 1


def test_a_full_range_pass_permanently_taints_that_range_for_future_tests(tmp_path: Path) -> None:
    """Regression test for the exact leakage bug this module exists to prevent:
    a single-range screening pass over Jan-Jul must block Jun-Jul from ever
    being used as a genuinely fresh test range afterward."""
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Baseline", role=UsageRole.SCREENING,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    result = check_range(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )

    assert result.allowed is False
    assert "screening" not in result.reason  # reason text doesn't need the word, but the block itself must fire


def test_test_range_is_spent_once_per_candidate_regardless_of_which_range(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )

    second_attempt = check_range(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 9, 1), end=date(2026, 9, 30),
    )
    different_candidate_fresh_range = check_range(
        store, candidate_name="Strict RSI", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 9, 1), end=date(2026, 9, 30),
    )
    different_candidate_stale_range = check_range(
        store, candidate_name="Strict RSI", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 7, 1), end=date(2026, 7, 31),
    )

    assert second_attempt.allowed is False
    assert "already has a test range" in second_attempt.reason
    assert different_candidate_fresh_range.allowed is True, (
        "a new candidate on a genuinely later, never-touched range must still be allowed"
    )
    assert different_candidate_stale_range.allowed is False, (
        "a new candidate cannot reuse the already-touched Jun-Jul range either"
    )


def test_record_usage_rejects_confirmed_label_combined_with_forced_override(tmp_path: Path) -> None:
    store = archive(tmp_path)
    with pytest.raises(ValueError, match="forced_override_reason"):
        record_usage(
            store, candidate_name="X", role=UsageRole.TEST,
            underlying_key=UNDERLYING, timeframe=TIMEFRAME,
            start=date(2026, 8, 1), end=date(2026, 8, 31),
            outcome_label="confirmed", forced_override_reason="ran out of patience",
        )


def test_record_usage_rejects_invalid_outcome_label(tmp_path: Path) -> None:
    store = archive(tmp_path)
    with pytest.raises(ValueError):
        record_usage(
            store, candidate_name="X", role=UsageRole.DEVELOPMENT,
            underlying_key=UNDERLYING, timeframe=TIMEFRAME,
            start=date(2026, 1, 1), end=date(2026, 1, 31),
            outcome_label="definitely-good",
        )


def test_classify_confirmation_eligible_when_test_ran_without_override(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )

    label = classify_confirmation(
        store, candidate_name="Morning entries",
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )

    assert label == "eligible_confirmed"


def test_classify_confirmation_is_exploratory_when_a_forced_override_was_used(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Rushed idea", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
        forced_override_reason="operator insisted despite reused range",
    )

    label = classify_confirmation(
        store, candidate_name="Rushed idea",
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )

    assert label == "exploratory"


def test_classify_confirmation_blocked_when_no_test_was_ever_recorded(tmp_path: Path) -> None:
    store = archive(tmp_path)

    label = classify_confirmation(
        store, candidate_name="Never tested", underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )

    assert label == "blocked_reused_test"


def test_forced_override_can_never_be_certified_as_confirmed_end_to_end(tmp_path: Path) -> None:
    """No matter what a caller passes as outcome_label, a forced-override test
    can never come back as anything but exploratory from the certifying function."""
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="X", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
        outcome_label="rejected", forced_override_reason="pressed ahead anyway",
    )

    assert classify_confirmation(
        store, candidate_name="X", underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    ) == "exploratory"


def test_research_context_for_ideation_never_contains_numeric_outcome_fields(tmp_path: Path) -> None:
    """The core anti-p-hacking mechanism: an idea generator must not be able
    to see net P&L, win rate, drawdown, or trade counts anywhere in its context."""
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
        outcome_label="confirmed",
        notes="net_pnl=1064.90 drawdown=5994.10 trades=31",  # even if notes leak numbers, top-level keys must not
    )

    context = research_context_for_ideation(store)

    assert "usage_history" in context
    entry = context["usage_history"][0]
    forbidden_keys = {"net_pnl", "win_rate", "drawdown", "max_drawdown", "trades", "profit_factor"}
    assert forbidden_keys.isdisjoint(entry.keys())
    assert set(entry.keys()) == {
        "candidate_name", "role", "underlying_key", "timeframe",
        "range_start", "range_end", "outcome_label",
    }
    assert entry["outcome_label"] == "confirmed"
    assert entry["candidate_name"] == "Morning entries"


def test_research_context_for_evaluation_includes_full_row_detail(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
        outcome_label="confirmed", notes="31 trades, +1064.90 net",
    )

    context = research_context_for_evaluation(store)

    entry = context["usage_history"][0]
    assert entry["notes"] == "31 trades, +1064.90 net"
    assert entry["outcome_label"] == "confirmed"
    assert "id" in entry and "recorded_at" in entry


def test_export_ledger_json_writes_a_readable_file(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Baseline", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 3, 31),
    )

    target = export_ledger_json(store, tmp_path / "research" / "range_usage_ledger.json")

    assert target.exists()
    contents = target.read_text(encoding="utf-8")
    assert "Baseline" in contents
    assert contents.endswith("\n")


def test_range_usage_is_a_frozen_dataclass_with_expected_fields(tmp_path: Path) -> None:
    store = archive(tmp_path)
    usage = record_usage(
        store, candidate_name="Baseline", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 3, 31),
    )

    assert isinstance(usage, RangeUsage)
    assert usage.candidate_name == "Baseline"
    assert usage.role == UsageRole.DEVELOPMENT
    assert usage.range_start == date(2026, 1, 1)
    assert usage.range_end == date(2026, 3, 31)
    assert usage.id > 0


def test_check_range_rejects_start_after_end(tmp_path: Path) -> None:
    store = archive(tmp_path)

    result = check_range(
        store, candidate_name="X", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 3, 31), end=date(2026, 1, 1),
    )

    assert result.allowed is False


def test_initialize_ledger_seeds_a_conservative_row_from_existing_candles(tmp_path: Path) -> None:
    store = archive(tmp_path)
    _seed_candles(store, date(2026, 8, 3))

    initialize_ledger(store)

    blocked = check_range(
        store, candidate_name="Anything", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 8, 3), end=date(2026, 8, 3),
    )
    fresh = check_range(
        store, candidate_name="Anything", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 8, 4), end=date(2026, 8, 10),
    )

    assert blocked.allowed is False, "pre-existing candle history must not be certifiable as fresh"
    assert fresh.allowed is True


def test_initialize_ledger_is_idempotent_and_does_not_overwrite_real_history(tmp_path: Path) -> None:
    store = archive(tmp_path)
    _seed_candles(store, date(2026, 8, 3))
    record_usage(
        store, candidate_name="Baseline", role=UsageRole.SCREENING,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    initialize_ledger(store)
    initialize_ledger(store)

    rows = research_context_for_evaluation(store)["usage_history"]
    assert len(rows) == 1, "seeding must be a no-op once real history already exists for the scope"
    assert rows[0]["candidate_name"] == "Baseline"


def test_resolve_and_has_underlying_data(tmp_path: Path) -> None:
    store = archive(tmp_path)

    assert resolve_archive_date_range(store, UNDERLYING, TIMEFRAME) is None
    assert has_underlying_data(
        store, underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 8, 3), end=date(2026, 8, 3),
    ) is False

    _seed_candles(store, date(2026, 8, 3))

    assert resolve_archive_date_range(store, UNDERLYING, TIMEFRAME) == (date(2026, 8, 3), date(2026, 8, 3))
    assert has_underlying_data(
        store, underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 8, 1), end=date(2026, 8, 5),
    ) is True
    assert has_underlying_data(
        store, underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 9, 1), end=date(2026, 9, 5),
    ) is False


def test_verify_params_fingerprint_catches_a_renamed_parameter_set(tmp_path: Path) -> None:
    store = archive(tmp_path)
    first_fingerprint = compute_params_fingerprint('{"name": "X", "bullish_rsi_min": 60}')
    record_usage(
        store, candidate_name="X", role=UsageRole.DEVELOPMENT,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 1, 31),
        params_fingerprint=first_fingerprint,
    )

    same_params = verify_params_fingerprint(
        store, candidate_name="X", params_fingerprint=first_fingerprint
    )
    different_params = verify_params_fingerprint(
        store, candidate_name="X",
        params_fingerprint=compute_params_fingerprint('{"name": "X", "bullish_rsi_min": 65}'),
    )
    unseen_candidate = verify_params_fingerprint(
        store, candidate_name="Never seen before", params_fingerprint="anything"
    )

    assert same_params is None
    assert different_params is not None and "fingerprint mismatch" in different_params
    assert unseen_candidate is None


def test_compute_params_fingerprint_is_stable_regardless_of_key_order(tmp_path: Path) -> None:
    a = compute_params_fingerprint('{"name": "X", "bullish_rsi_min": 60}')
    b = compute_params_fingerprint('{"bullish_rsi_min": 60, "name": "X"}')
    c = compute_params_fingerprint('{"name": "X", "bullish_rsi_min": 61}')

    assert a == b
    assert a != c


def test_reserve_test_range_inserts_immediately_so_a_second_reservation_is_blocked(
    tmp_path: Path,
) -> None:
    store = archive(tmp_path)

    first = reserve_test_range(
        store, candidate_name="First", underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )
    second = reserve_test_range(
        store, candidate_name="Second", underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 15), end=date(2026, 7, 15),
    )

    assert first is not None
    assert second is None, (
        "a reservation must be visible to check_range immediately, before any finalize call -- "
        "this is what protects against a crash or a concurrent second reservation"
    )


def test_reserve_and_finalize_round_trip_is_what_classify_confirmation_certifies(
    tmp_path: Path,
) -> None:
    store = archive(tmp_path)

    reservation = reserve_test_range(
        store, candidate_name="Morning entries", underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )
    assert reservation is not None

    finalize_test_reservation(store, reservation, outcome_label="rejected", notes="12 trades, net -340")

    classification = classify_confirmation(
        store, candidate_name="Morning entries",
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )
    assert classification == "eligible_confirmed"


def test_reserve_test_range_refuses_a_blocked_range_without_forced_override(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Prior", role=UsageRole.SCREENING,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 1, 1), end=date(2026, 7, 31),
    )

    blocked = reserve_test_range(
        store, candidate_name="X", underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
    )
    forced = reserve_test_range(
        store, candidate_name="X", underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
        forced_override_reason="operator insisted",
    )

    assert blocked is None
    assert forced is not None
    assert forced.forced_override_reason == "operator insisted"


def test_export_ledger_json_redacted_omits_notes_and_numeric_leakage(tmp_path: Path) -> None:
    store = archive(tmp_path)
    record_usage(
        store, candidate_name="Morning entries", role=UsageRole.TEST,
        underlying_key=UNDERLYING, timeframe=TIMEFRAME,
        start=date(2026, 6, 1), end=date(2026, 7, 31),
        outcome_label="confirmed", notes="net_pnl=1064.90 drawdown=5994.10 trades=31",
    )

    redacted_target = export_ledger_json(
        store, tmp_path / "research" / "redacted.json", redact=True
    )
    full_target = export_ledger_json(store, tmp_path / "research" / "full.json", redact=False)

    redacted_contents = redacted_target.read_text(encoding="utf-8")
    full_contents = full_target.read_text(encoding="utf-8")
    assert "1064.90" not in redacted_contents
    assert "notes" not in redacted_contents
    assert "1064.90" in full_contents
