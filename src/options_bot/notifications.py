"""Optional alert-only notifications; no execution controls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TelegramNotifier:
    token: str
    chat_id: str

    def send(self, text: str) -> None:
        if not self.token or not self.chat_id:
            raise ValueError("Telegram credentials are missing")
        payload = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
        request = Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError(f"Telegram returned HTTP {response.status}")
