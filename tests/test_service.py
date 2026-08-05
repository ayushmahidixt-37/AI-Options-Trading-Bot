from pathlib import Path

import pytest

from options_bot.config import Settings
from options_bot.runner import build_application
from options_bot.service import AlreadyRunning, PaperService


def test_single_instance_lock(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    application = build_application(settings)
    first = PaperService(application)
    second = PaperService(application)
    first.acquire_lock()
    try:
        with pytest.raises(AlreadyRunning):
            second.acquire_lock()
    finally:
        first.release_lock()
    assert not first.lock_path.exists()


def test_failed_lock_release_does_not_unlink_active_lock(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    application = build_application(settings)
    first = PaperService(application)
    second = PaperService(application)
    first.acquire_lock()
    try:
        with pytest.raises(AlreadyRunning):
            second.acquire_lock()
        second.release_lock()
        assert first.lock_path.exists()
    finally:
        first.release_lock()
