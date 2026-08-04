from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from options_bot.config import ConfigurationError, Settings, load_env_file


def test_safe_defaults_are_paper_only(tmp_path: Path) -> None:
    settings = Settings.from_env({"DATA_DIR": str(tmp_path)})
    assert settings.trading_mode == "paper"
    assert settings.live_trading_enabled is False
    assert settings.auto_start is False
    assert settings.max_lots_per_trade == 1
    assert settings.max_open_positions == 1


@pytest.mark.parametrize(
    "unsafe",
    [
        {"TRADING_MODE": "live"},
        {"LIVE_TRADING_ENABLED": "true"},
        {"AUTO_START": "true"},
    ],
)
def test_unsafe_modes_fail_configuration(unsafe: dict[str, str], tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({"DATA_DIR": str(tmp_path), **unsafe})


def test_invalid_session_order_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="strictly increasing"):
        Settings.from_env(
            {
                "DATA_DIR": str(tmp_path),
                "ENTRY_START_IST": "15:01",
                "ENTRY_CUTOFF_IST": "15:00",
            }
        )


def test_config_file_does_not_override_environment() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "bot.env")
        path.write_text("MAX_TRADES_PER_DAY=9\n", encoding="utf-8")
        environ = {"MAX_TRADES_PER_DAY": "2"}
        load_env_file(path, environ)
        assert environ["MAX_TRADES_PER_DAY"] == "2"
