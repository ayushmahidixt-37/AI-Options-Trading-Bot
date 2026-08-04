from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from options_bot.clock import MarketClock
from options_bot.config import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env({"DATA_DIR": str(tmp_path)})


def test_entry_window_and_force_exit(tmp_path: Path) -> None:
    clock = MarketClock(settings(tmp_path))
    ist = ZoneInfo("Asia/Kolkata")
    monday = datetime(2026, 8, 3, 10, 0, tzinfo=ist)
    late = datetime(2026, 8, 3, 15, 21, tzinfo=ist)
    weekend = datetime(2026, 8, 2, 10, 0, tzinfo=ist)
    assert clock.entries_allowed(monday)
    assert not clock.entries_allowed(late)
    assert not clock.entries_allowed(weekend)
    assert clock.force_exit_due(late)
