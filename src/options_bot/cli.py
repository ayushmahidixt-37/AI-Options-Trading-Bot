"""Operational command line interface for the paper-only server release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .config import ConfigurationError, Settings, load_env_file
from .health import healthcheck
from .runner import build_application
from .service import AlreadyRunning, PaperService


def _settings(path: str | None) -> Settings:
    if path:
        load_env_file(Path(path), os.environ)
    return Settings.from_env()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="options-bot")
    root.add_argument("--config", help="Path to the non-secret bot.env file")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-config")
    commands.add_parser("init-db")
    commands.add_parser("healthcheck")
    commands.add_parser("status")
    serve = commands.add_parser("serve")
    serve.add_argument("--heartbeat-seconds", type=float, default=30)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = _settings(args.config)
        if args.command == "validate-config":
            print("Configuration valid: paper mode, live disabled, auto-start disabled")
            return 0
        app = build_application(settings)
        if args.command == "init-db":
            print(f"Paper database initialized: {settings.database_path}")
            return 0
        if args.command == "healthcheck":
            report = healthcheck(settings, app.ledger)
            print(json.dumps({"ok": report.ok, "checks": report.checks}, sort_keys=True))
            return 0 if report.ok else 1
        if args.command == "serve":
            return PaperService(app, interval_seconds=args.heartbeat_seconds).run()
        account = dict(app.ledger.account())
        positions = [dict(row) for row in app.ledger.open_positions()]
        print(json.dumps({"mode": "paper", "account": account, "positions": positions}, default=str))
        return 0
    except (AlreadyRunning, ConfigurationError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
