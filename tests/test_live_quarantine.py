import pytest

from options_bot.execution.live_angel import (
    REQUIRED_ACKNOWLEDGEMENT,
    AngelOneLiveExecutor,
    LiveExecutionUnavailable,
    LiveSafetyGate,
    build_live_executor,
)


def test_live_executor_is_unavailable() -> None:
    with pytest.raises(LiveExecutionUnavailable, match="paper application"):
        build_live_executor()


@pytest.mark.parametrize(
    "gate",
    [
        LiveSafetyGate("paper", True, REQUIRED_ACKNOWLEDGEMENT, True),
        LiveSafetyGate("live", False, REQUIRED_ACKNOWLEDGEMENT, True),
        LiveSafetyGate("live", True, "wrong", True),
        LiveSafetyGate("live", True, REQUIRED_ACKNOWLEDGEMENT, False),
    ],
)
def test_each_live_gate_fails_closed(gate: LiveSafetyGate) -> None:
    with pytest.raises(LiveExecutionUnavailable):
        AngelOneLiveExecutor(object(), gate)


def test_retained_adapter_calls_sdk_only_after_all_gates() -> None:
    class FakeSmartApi:
        def placeOrder(self, payload):
            return {"status": True, "payload": payload}

    gate = LiveSafetyGate("live", True, REQUIRED_ACKNOWLEDGEMENT, True)
    executor = AngelOneLiveExecutor(FakeSmartApi(), gate)
    assert executor.place_order({"symbol": "TEST"})["status"] is True
