"""Quarantined placeholder for future reviewed Angel One live execution.

The legacy notebook retains the original prototype. This server release does
not import or instantiate a live executor and intentionally exposes no order
submission implementation.
"""


class LiveExecutionUnavailable(RuntimeError):
    """Raised whenever this paper-only release is asked for live execution."""


def build_live_executor() -> None:
    raise LiveExecutionUnavailable(
        "Live execution is unavailable in this paper-only release"
    )
