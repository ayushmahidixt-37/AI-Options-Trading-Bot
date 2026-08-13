from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from options_bot.market_archive import MarketArchive
from options_bot.research_ledger import (
    RangeUsage,
    UsageRole,
    check_range,
    classify_confirmation,
    export_ledger_json,
    record_usage,
    research_context_for_evaluation,
    research_context_for_ideation,
)

UNDERLYING = "NSE_INDEX|Nifty 50"
TIMEFRAME = "FIVE_MINUTE"


def archive(tmp_path: Path) -> MarketArchive:
    result = MarketArchive(tmp_path / "market-data.sqlite3")
    result.initialize()
    return result


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
