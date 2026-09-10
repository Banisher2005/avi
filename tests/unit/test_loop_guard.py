"""Unit tests for Phase 4 Agent Loop Guard."""


from avi.agent.loop_guard import Invocation, LoopGuard, LoopGuardConfig
from avi.capabilities.models import CapabilityResult, ExecutionStatus


class TestLoopGuard:
    """Test LoopGuard detection of identical calls, ping-pong cycles, and limits."""

    def test_single_invocations_pass(self):
        guard = LoopGuard()
        inv1 = Invocation(capability_name="desktop.screenshot", arguments={})
        res1 = guard.check_invocation(inv1)
        assert res1.is_loop is False
        guard.record_invocation(inv1)
        guard.record_result(inv1, CapabilityResult(success=True, status=ExecutionStatus.SUCCESS))

        inv2 = Invocation(capability_name="desktop.open_file", arguments={"path": "/tmp/test.png"})
        res2 = guard.check_invocation(inv2)
        assert res2.is_loop is False

    def test_identical_call_limit(self):
        guard = LoopGuard(LoopGuardConfig(max_identical_invocations=2))
        inv = Invocation(capability_name="desktop.volume.set", arguments={"level": 50})

        # 1st call: OK
        assert guard.check_invocation(inv).is_loop is False
        guard.record_invocation(inv)

        # 2nd call: OK
        assert guard.check_invocation(inv).is_loop is False
        guard.record_invocation(inv)

        # 3rd call: BLOCKED
        check = guard.check_invocation(inv)
        assert check.is_loop is True
        assert check.loop_type == "identical_call"
        assert "identical arguments" in check.reason

    def test_ping_pong_alternating_cycles(self):
        # max_ping_pong_cycles = 1 means as soon as A-B-A-B is attempted, trigger
        guard = LoopGuard(LoopGuardConfig(max_ping_pong_cycles=1))
        inv_a = Invocation(capability_name="action_a", arguments={"step": 1})
        inv_b = Invocation(capability_name="action_b", arguments={"step": 2})

        # A
        assert guard.check_invocation(inv_a).is_loop is False
        guard.record_invocation(inv_a)
        # B
        assert guard.check_invocation(inv_b).is_loop is False
        guard.record_invocation(inv_b)
        # A
        assert guard.check_invocation(inv_a).is_loop is False
        guard.record_invocation(inv_a)
        # B -> detected ping pong!
        check = guard.check_invocation(inv_b)
        assert check.is_loop is True
        assert check.loop_type == "ping_pong"

    def test_excessive_tool_calls_boundary(self):
        guard = LoopGuard(LoopGuardConfig(max_total_invocations=3))
        for i in range(3):
            inv = Invocation(capability_name=f"action_{i}")
            assert guard.check_invocation(inv).is_loop is False
            guard.record_invocation(inv)

        # 4th call exceeds max_total_invocations (3)
        inv4 = Invocation(capability_name="action_4")
        check = guard.check_invocation(inv4)
        assert check.is_loop is True
        assert check.loop_type == "excessive_calls"

    def test_repeated_failure_detection(self):
        guard = LoopGuard(LoopGuardConfig(max_consecutive_failures=2))
        inv1 = Invocation(capability_name="action_fail_1")
        guard.record_invocation(inv1)
        guard.record_result(inv1, CapabilityResult(success=False, status=ExecutionStatus.FAILED))

        inv2 = Invocation(capability_name="action_fail_2")
        guard.record_invocation(inv2)
        guard.record_result(inv2, CapabilityResult(success=False, status=ExecutionStatus.FAILED))

        # 3rd action should be blocked due to consecutive failures
        inv3 = Invocation(capability_name="action_3")
        check = guard.check_invocation(inv3)
        assert check.is_loop is True
        assert check.loop_type == "repeated_failure"
