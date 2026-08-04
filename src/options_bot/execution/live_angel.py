"""Separately gated Angel One live executor preserved for future review.

The normal application never imports or constructs this class. The current
``Settings`` model rejects live mode before application composition, so this
adapter is retained as isolated code rather than a reachable runtime path.
"""

from __future__ import annotations

from dataclasses import dataclass

REQUIRED_ACKNOWLEDGEMENT = "I_UNDERSTAND_REAL_MONEY_IS_AT_RISK"


class LiveExecutionUnavailable(RuntimeError):
    """Raised unless every independent live-execution gate is satisfied."""


@dataclass(frozen=True)
class LiveSafetyGate:
    trading_mode: str
    enabled: bool
    acknowledgement: str
    explicit_cli_flag: bool

    def validate(self) -> None:
        if self.trading_mode != "live":
            raise LiveExecutionUnavailable("TRADING_MODE is not live")
        if not self.enabled:
            raise LiveExecutionUnavailable("Live execution is disabled")
        if self.acknowledgement != REQUIRED_ACKNOWLEDGEMENT:
            raise LiveExecutionUnavailable("Live risk acknowledgement is missing")
        if not self.explicit_cli_flag:
            raise LiveExecutionUnavailable("The explicit live CLI flag is missing")


class AngelOneLiveExecutor:
    """Minimal retained order adapter, unreachable from the paper application."""

    def __init__(self, smart_api: object, gate: LiveSafetyGate) -> None:
        gate.validate()
        self._smart_api = smart_api

    def place_order(self, payload: dict[str, object]) -> object:
        method = getattr(self._smart_api, "placeOrder", None)
        if not callable(method):
            raise LiveExecutionUnavailable("SmartAPI object has no placeOrder method")
        return method(payload)


def build_live_executor() -> None:
    """Compatibility entry point that always fails for the paper application."""
    raise LiveExecutionUnavailable("Live execution is unavailable in the paper application")
