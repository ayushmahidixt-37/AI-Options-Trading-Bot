"""Long-running, signal-aware paper service with a single-instance lock."""

from __future__ import annotations

import json
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import TextIO

from .health import healthcheck
from .runner import Application


class AlreadyRunning(RuntimeError):
    """Raised when another process holds the runtime lock."""


@dataclass
class PaperService:
    application: Application
    interval_seconds: float = 30
    _stopping: bool = field(default=False, init=False)
    _lock_handle: TextIO | None = field(default=None, init=False)

    @property
    def lock_path(self) -> Path:
        return self.application.settings.data_dir / "options-bot.lock"

    def acquire_lock(self) -> None:
        import fcntl

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise AlreadyRunning("Another options-bot process is already running") from exc
        handle.write(str(__import__("os").getpid()))
        handle.flush()
        self._lock_handle = handle

    def release_lock(self) -> None:
        if self._lock_handle is not None:
            import fcntl

            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None
            self.lock_path.unlink(missing_ok=True)

    def stop(self, _signal: int | None = None, _frame: FrameType | None = None) -> None:
        self._stopping = True

    def run(self) -> int:
        self.acquire_lock()
        previous_term = signal.signal(signal.SIGTERM, self.stop)
        previous_int = signal.signal(signal.SIGINT, self.stop)
        try:
            print("Paper service ready; automatic entries are disabled", flush=True)
            while not self._stopping:
                report = healthcheck(self.application.settings, self.application.ledger)
                print(json.dumps({"event": "heartbeat", "ok": report.ok, **report.checks}), flush=True)
                if not report.ok:
                    return 1
                time.sleep(self.interval_seconds)
            return 0
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
            self.release_lock()
