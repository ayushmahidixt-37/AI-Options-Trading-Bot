"""Known, scheduled macro-event dates likely to move NIFTY intraday --
RBI Monetary Policy Committee announcements, US FOMC rate decisions, and
the Union Budget. Deliberately *not* a news/sentiment feed: every date
here was publicly known in advance, so this is checkable at backtest time
without needing a historical news corpus (which this project doesn't have)
or a live NLP pipeline (which wouldn't fit the memory-constrained Termux
runtime this project ultimately targets). Unplanned shocks -- a surprise
statement, an unscheduled announcement -- are explicitly out of scope;
there's no data source for those yet.

Dates verified via web search against primary/near-primary sources
(federalreserve.gov's own calendar for FOMC; RBI press coverage for MPC,
since rbi.org.in's own schedule pages didn't surface directly) on
2026-08-22. RBI dates are the *last* day of each 3-day MPC meeting (the
day the rate decision is actually announced); FOMC dates are the *second*
day of each 2-day meeting for the same reason. Extend this list as new
schedules are published -- both central banks publish theirs many months
ahead.
"""

from __future__ import annotations

from datetime import date

RBI_MPC_ANNOUNCEMENT_DATES: frozenset[date] = frozenset(
    {
        date(2024, 10, 9),
        date(2024, 12, 6),
        date(2025, 2, 7),
        date(2025, 4, 9),
        date(2025, 6, 6),
        date(2025, 8, 6),  # rescheduled from Aug 5-7 to Aug 4-6
        date(2025, 10, 1),
        date(2025, 12, 5),
        date(2026, 2, 6),
        date(2026, 4, 8),
        date(2026, 6, 5),
        date(2026, 8, 5),
    }
)

FOMC_ANNOUNCEMENT_DATES: frozenset[date] = frozenset(
    {
        date(2024, 1, 31),
        date(2024, 3, 20),
        date(2024, 5, 1),
        date(2024, 6, 12),
        date(2024, 7, 31),
        date(2024, 9, 18),
        date(2024, 11, 7),
        date(2024, 12, 18),
        date(2025, 1, 29),
        date(2025, 3, 19),
        date(2025, 5, 7),
        date(2025, 6, 18),
        date(2025, 7, 30),
        date(2025, 9, 17),
        date(2025, 10, 29),
        date(2025, 12, 10),
        date(2026, 1, 28),
        date(2026, 3, 18),
        date(2026, 4, 29),
        date(2026, 6, 17),
        date(2026, 7, 29),
        date(2026, 9, 16),
        date(2026, 10, 28),
        date(2026, 12, 9),
    }
)

# The Union Budget has been presented on February 1 every year since 2017
# (a fixed convention, not something that needs per-year verification) --
# except when February 1 falls on a weekend, when it moves to the nearest
# preceding trading day.
UNION_BUDGET_DATES: frozenset[date] = frozenset(
    {
        date(2025, 2, 1),  # Saturday -- markets were specially opened for this budget session
        date(2026, 2, 2),  # Feb 1, 2026 is a Sunday; nearest trading day
    }
)

KNOWN_MACRO_EVENT_DATES: frozenset[date] = (
    RBI_MPC_ANNOUNCEMENT_DATES | FOMC_ANNOUNCEMENT_DATES | UNION_BUDGET_DATES
)


def is_macro_event_window(day: date, *, include_next_day: bool = True) -> bool:
    """True if ``day`` is a known macro-event day, or (by default) the
    trading day immediately after one -- these announcements are typically
    made mid-session or after a US market close, so volatility often
    carries into the next Indian trading session too."""
    if day in KNOWN_MACRO_EVENT_DATES:
        return True
    if include_next_day:
        from datetime import timedelta

        return (day - timedelta(days=1)) in KNOWN_MACRO_EVENT_DATES
    return False
