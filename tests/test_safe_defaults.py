import json
from pathlib import Path


def test_notebook_has_safe_configuration_defaults() -> None:
    notebook = json.loads(Path("bot30.ipynb.txt").read_text())
    source = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])

    assert "DRY_RUN        = True" in source
    assert '"AUTO_MODE": False' in source
    assert "ENGINE_ENABLED = False" in source
    assert 'os.environ["DRY_RUN"] = "0"' not in source
    assert 'globals()["DRY_RUN"] = False' not in source


def test_notebook_outputs_are_cleared() -> None:
    notebook = json.loads(Path("bot30.ipynb.txt").read_text())
    assert all(not cell.get("outputs") for cell in notebook["cells"])
