"""Validated server configuration with paper-only safety invariants."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(ValueError):
    """Raised when server configuration is incomplete or unsafe."""


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _decimal(env: Mapping[str, str], name: str, default: float, minimum: float = 0) -> float:
    try:
        value = float(env.get(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _clock(value: str, name: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour, minute)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must use HH:MM format") from exc


def load_env_file(path: str | Path, environ: dict[str, str] | None = None) -> Path:
    """Load a non-secret ``KEY=VALUE`` file without overriding the environment."""
    target = os.environ if environ is None else environ
    source = Path(path)
    if not source.is_file():
        raise ConfigurationError(f"Configuration file not found: {source}")
    for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(f"Malformed configuration line {number}")
        key, value = line.split("=", 1)
        target.setdefault(key.strip(), value.strip())
    return source


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    credentials_path: Path
    trading_mode: str
    live_trading_enabled: bool
    auto_start: bool
    starting_capital: float
    max_lots_per_trade: int
    max_open_positions: int
    max_trades_per_day: int
    max_loss_per_trade: float
    max_daily_net_loss: float
    max_quote_age_seconds: int
    paper_slippage_bps: int
    paper_fee_per_order: float
    timezone: ZoneInfo
    entry_start: time
    entry_cutoff: time
    force_exit: time

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        data_dir = Path(values.get("DATA_DIR", "/var/lib/ai-options-bot"))
        timezone_name = values.get("TIMEZONE", "Asia/Kolkata")
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(f"Unknown TIMEZONE: {timezone_name}") from exc
        settings = cls(
            data_dir=data_dir,
            database_path=Path(values.get("DATABASE_PATH", str(data_dir / "paper.sqlite3"))),
            credentials_path=Path(values.get("CREDENTIALS_PATH", "/etc/ai-options-bot/credentials.env")),
            trading_mode=values.get("TRADING_MODE", "paper").strip().lower(),
            live_trading_enabled=_boolean(values, "LIVE_TRADING_ENABLED", False),
            auto_start=_boolean(values, "AUTO_START", False),
            starting_capital=_decimal(values, "PAPER_STARTING_CAPITAL", 100000, 1),
            max_lots_per_trade=_integer(values, "MAX_LOTS_PER_TRADE", 1, 1),
            max_open_positions=_integer(values, "MAX_OPEN_POSITIONS", 1, 1),
            max_trades_per_day=_integer(values, "MAX_TRADES_PER_DAY", 3, 1),
            max_loss_per_trade=_decimal(values, "MAX_LOSS_PER_TRADE", 400, 0.01),
            max_daily_net_loss=_decimal(values, "MAX_DAILY_NET_LOSS", 1000, 0.01),
            max_quote_age_seconds=_integer(values, "MAX_QUOTE_AGE_SECONDS", 3, 1),
            paper_slippage_bps=_integer(values, "PAPER_SLIPPAGE_BPS", 25, 0),
            paper_fee_per_order=_decimal(values, "PAPER_FEE_PER_ORDER", 20, 0),
            timezone=timezone,
            entry_start=_clock(values.get("ENTRY_START_IST", "09:25"), "ENTRY_START_IST"),
            entry_cutoff=_clock(values.get("ENTRY_CUTOFF_IST", "15:00"), "ENTRY_CUTOFF_IST"),
            force_exit=_clock(values.get("FORCE_EXIT_IST", "15:20"), "FORCE_EXIT_IST"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.trading_mode != "paper":
            raise ConfigurationError("This release supports TRADING_MODE=paper only")
        if self.live_trading_enabled:
            raise ConfigurationError("LIVE_TRADING_ENABLED must remain false")
        if self.auto_start:
            raise ConfigurationError("AUTO_START must remain false; start paper entries explicitly")
        if not self.entry_start < self.entry_cutoff < self.force_exit:
            raise ConfigurationError("Entry and force-exit times must be strictly increasing")
