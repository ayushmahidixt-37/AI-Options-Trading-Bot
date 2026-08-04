import pytest

from options_bot.execution.live_angel import LiveExecutionUnavailable, build_live_executor


def test_live_executor_is_unavailable() -> None:
    with pytest.raises(LiveExecutionUnavailable, match="paper-only"):
        build_live_executor()
