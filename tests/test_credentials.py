from pathlib import Path

import pytest

from options_bot.credentials import CredentialFileError, load_credentials


def test_loads_expected_names(tmp_path: Path) -> None:
    path = tmp_path / "secrets"
    path.write_text("ANGEL_API_KEY=test-value\n# comment\nOPENAI_API_KEY=\n")

    assert load_credentials(path) == {
        "ANGEL_API_KEY": "test-value",
        "OPENAI_API_KEY": "",
    }


@pytest.mark.parametrize("content", ["NOT_A_LINE", "UNEXPECTED_NAME=value", "=value"])
def test_rejects_malformed_or_unknown_lines(tmp_path: Path, content: str) -> None:
    path = tmp_path / "secrets"
    path.write_text(content)

    with pytest.raises(CredentialFileError):
        load_credentials(path)


def test_rejects_duplicate_names(tmp_path: Path) -> None:
    path = tmp_path / "secrets"
    path.write_text("ANGEL_API_KEY=first\nANGEL_API_KEY=second\n")

    with pytest.raises(CredentialFileError, match="Duplicate credential name"):
        load_credentials(path)


def test_missing_file_has_clear_path_without_values(tmp_path: Path) -> None:
    path = tmp_path / "missing"

    with pytest.raises(FileNotFoundError, match="Credential file not found at"):
        load_credentials(path)
