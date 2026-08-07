from pathlib import Path


def test_termux_launcher_requires_latest_main_and_does_not_hide_pull_failures() -> None:
    script = Path("scripts/termux_web.sh").read_text(encoding="utf-8")

    assert "git fetch origin main" in script
    assert "git checkout main" in script
    assert "git merge --ff-only origin/main" in script
    assert "git pull --ff-only || true" not in script
    assert "Running repository version:" in script
