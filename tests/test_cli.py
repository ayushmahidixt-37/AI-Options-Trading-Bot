from __future__ import annotations

import json
from pathlib import Path

from options_bot.cli import main


def config(tmp_path: Path) -> Path:
    path = tmp_path / "bot.env"
    path.write_text(
        f"DATA_DIR={tmp_path}\nDATABASE_PATH={tmp_path / 'paper.sqlite3'}\n",
        encoding="utf-8",
    )
    return path


def test_validate_init_health_and_status(tmp_path: Path, capsys) -> None:
    path = config(tmp_path)
    assert main(["--config", str(path), "validate-config"]) == 0
    assert main(["--config", str(path), "init-db"]) == 0
    assert main(["--config", str(path), "healthcheck"]) == 0
    health = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert health["ok"] is True
    assert health["checks"]["mode"] == "paper"
    assert main(["--config", str(path), "status"]) == 0


def test_cli_rejects_live_mode(tmp_path: Path, capsys) -> None:
    path = tmp_path / "unsafe.env"
    path.write_text("TRADING_MODE=live\n", encoding="utf-8")
    assert main(["--config", str(path), "validate-config"]) == 2
    assert "paper only" in capsys.readouterr().out
