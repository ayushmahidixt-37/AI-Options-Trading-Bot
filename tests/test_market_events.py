from __future__ import annotations

from datetime import date

from options_bot.market_events import (
    FOMC_ANNOUNCEMENT_DATES,
    RBI_MPC_ANNOUNCEMENT_DATES,
    UNION_BUDGET_DATES,
    is_macro_event_window,
)


def test_known_event_sets_are_non_empty_and_disjoint_from_far_future() -> None:
    assert len(RBI_MPC_ANNOUNCEMENT_DATES) > 0
    assert len(FOMC_ANNOUNCEMENT_DATES) > 0
    assert len(UNION_BUDGET_DATES) > 0
    assert date(2030, 1, 1) not in RBI_MPC_ANNOUNCEMENT_DATES | FOMC_ANNOUNCEMENT_DATES


def test_is_macro_event_window_matches_the_event_day_itself() -> None:
    event_day = next(iter(RBI_MPC_ANNOUNCEMENT_DATES))
    assert is_macro_event_window(event_day) is True


def test_is_macro_event_window_matches_the_following_day_by_default() -> None:
    event_day = date(2024, 10, 9)  # a known RBI MPC announcement date
    assert event_day in RBI_MPC_ANNOUNCEMENT_DATES
    day_after = date(2024, 10, 10)
    assert is_macro_event_window(day_after) is True
    assert is_macro_event_window(day_after, include_next_day=False) is False


def test_is_macro_event_window_false_for_an_ordinary_day() -> None:
    ordinary_day = date(2026, 3, 3)
    assert ordinary_day not in RBI_MPC_ANNOUNCEMENT_DATES | FOMC_ANNOUNCEMENT_DATES | UNION_BUDGET_DATES
    assert is_macro_event_window(ordinary_day) is False
