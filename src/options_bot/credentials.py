"""Strict loading of credentials kept outside the repository."""

from __future__ import annotations

from pathlib import Path


DEFAULT_CREDENTIALS_PATH = Path("/etc/ai-options-bot/credentials.env")
EXPECTED_CREDENTIAL_NAMES = frozenset(
    {
        "ANGEL_API_KEY",
        "ANGEL_CLIENT_CODE",
        "ANGEL_PASSWORD",
        "ANGEL_TOTP_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "OPENAI_API_KEY",
        "UPSTOX_API_KEY",
        "UPSTOX_API_SECRET",
        "UPSTOX_ACCESS_TOKEN",
    }
)


class CredentialFileError(ValueError):
    """Raised when a credential file does not satisfy the strict format."""


def load_credentials(path: str | Path = DEFAULT_CREDENTIALS_PATH) -> dict[str, str]:
    """Load only known credentials from a strict ``NAME=value`` file.

    Blank lines and lines beginning with ``#`` are ignored. Values are returned
    verbatim except for surrounding whitespace; this function never logs them.
    """
    credential_path = Path(path)
    if not credential_path.is_file():
        raise FileNotFoundError(
            f"Credential file not found at {credential_path}. "
            "Create it outside Git before starting the bot."
        )

    credentials: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        credential_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise CredentialFileError(
                f"Malformed credential line {line_number}: expected NAME=value"
            )
        name, value = (part.strip() for part in line.split("=", 1))
        if not name or name not in EXPECTED_CREDENTIAL_NAMES:
            raise CredentialFileError(
                f"Unknown credential name on line {line_number}"
            )
        if name in credentials:
            raise CredentialFileError(
                f"Duplicate credential name on line {line_number}: {name}"
            )
        credentials[name] = value
    return credentials
