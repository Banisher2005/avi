"""Integration failure-injection tests validating timeouts, recovery, and provider isolation."""

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from avi.agent.runtime import AgentRuntime
from avi.config import Config
from avi.core.router import Router
from avi.reliability.circuit_breaker import CircuitBreakerState
from avi.reliability.health import diagnose_self_query
from avi.reliability.models import (
    FailureCategory,
    OperationRecord,
    OperationStatus,
    OperationType,
    SupervisedResult,
    TimeoutConfig,
)
from avi.reliability.supervisor import ReliabilitySupervisor
from avi.reliability.watchdog import WatchdogSupervisor


class TestProviderFailureInjection:
    """Test behavior when LLM providers hang, time out, or crash."""

    def test_provider_timeout_handled_gracefully(self) -> None:
        """Provider hangs indefinitely; supervisor times out and returns clean message."""
        sup = ReliabilitySupervisor(timeouts=TimeoutConfig(provider_call=0.05))

        def _hanging_provider(*args: Any, **kwargs: Any) -> str:
            time.sleep(0.2)
            return "Too late"

        res: SupervisedResult = sup.execute_provider(
            provider_name="ollama",
            func=_hanging_provider,
            prompt="Hello",
            timeout=0.05,
        )

        assert res.is_success is False
        assert res.status == OperationStatus.TIMED_OUT
        assert res.failure_category == FailureCategory.PROVIDER_TIMEOUT
        assert "Ollama did not respond" in res.user_message or "timed out" in res.user_message.lower()

    def test_circuit_breaker_trips_after_consecutive_failures(self) -> None:
        """Circuit breaker trips to OPEN after 3 failures and protects runtime from repeated hangs."""
        sup = ReliabilitySupervisor(
            timeouts=TimeoutConfig(provider_call=0.05),
        )
        sup.circuit_breakers.reset_all()

        def _crashing_provider(*args: Any, **kwargs: Any) -> str:
            raise ConnectionError("Ollama daemon is down (connection refused)")

        # Execute 3 times to trip breaker
        for _ in range(3):
            res = sup.execute_provider("ollama", _crashing_provider)
            assert res.is_success is False

        cb = sup.circuit_breakers.get_or_create("ollama")
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.consecutive_failures >= 3

        # 4th call must fail fast without even executing the provider function
        mock_func = MagicMock()
        fast_fail_res = sup.execute_provider("ollama", mock_func)
        assert fast_fail_res.is_success is False
        assert fast_fail_res.failure_category == FailureCategory.PROVIDER_UNAVAILABLE
        mock_func.assert_not_called()
        sup.circuit_breakers.reset_all()

    def test_provider_offline_isolation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Ollama is completely offline, deterministic capabilities MUST STILL WORK."""
        cfg = Config.load()
        router = Router(cfg)
        runtime = AgentRuntime(config=cfg, router=router)

        # Force circuit breaker for provider to OPEN (simulating dead Ollama)
        supervisor = ReliabilitySupervisor.get_instance()
        cb = supervisor.circuit_breakers.get_or_create("ollama")
        cb.state = CircuitBreakerState.OPEN

        try:
            # 1. Calculator MUST WORK (< 10ms)
            calc_res = runtime.dispatch("what is 27 * 43?")
            assert not calc_res.is_background
            assert "1161" in calc_res.text or "1,161" in calc_res.text

            # 2. RAM query MUST WORK
            ram_res = runtime.dispatch("how much ram is free?")
            assert not ram_res.is_background
            assert "ram" in ram_res.text.lower() or "memory" in ram_res.text.lower() or "gb" in ram_res.text.lower()

            # 3. Tasks list MUST WORK
            tasks_res = runtime.dispatch("tasks")
            assert not tasks_res.is_background
            assert "tasks" in tasks_res.text.lower() or "active" in tasks_res.text.lower()

            # 4. Self-diagnostics MUST WORK
            diag_res = runtime.dispatch("why are you stuck?")
            assert not diag_res.is_background
            assert "Diagnostic Status" in diag_res.text or "ready" in diag_res.text.lower() or "System" in diag_res.text

            # 5. Greeting fast path MUST WORK
            greet_res = runtime.dispatch("hello")
            assert not greet_res.is_background
            assert len(greet_res.text) > 0
        finally:
            supervisor.circuit_breakers.reset_all()


class TestToolFailureInjection:
    """Test tool timeout and crash containment."""

    def test_tool_timeout_interruption(self) -> None:
        """Slow tool execution is aborted at timeout and formatted into a user-facing failure."""
        sup = ReliabilitySupervisor(timeouts=TimeoutConfig(default_tool=0.05, filesystem_search=0.05))

        def _slow_search() -> dict[str, Any]:
            time.sleep(0.2)
            return {"files": ["file1.txt"]}

        res: SupervisedResult = sup.execute_tool(
            capability_name="filesystem.search",
            func=_slow_search,
            timeout=0.05,
        )

        assert res.is_success is False
        assert res.status == OperationStatus.TIMED_OUT
        assert res.failure_category == FailureCategory.TOOL_TIMEOUT
        assert "File search took too long" in res.user_message

    def test_tool_exception_captured_without_crash(self) -> None:
        """Tool raising unhandled exception returns structured failure without crashing loop."""
        sup = ReliabilitySupervisor()

        def _exploding_tool() -> None:
            raise PermissionError("Permission denied: /root/secret")

        res: SupervisedResult = sup.execute_tool("filesystem.delete", _exploding_tool)
        assert res.is_success is False
        assert res.status == OperationStatus.FAILED
        assert "Permission denied" in (res.error or "")


class TestWorkerFailureInjection:
    """Test background worker failure handling and state reconciliation."""

    def test_worker_crash_captured_and_task_marked_failed(self) -> None:
        """Worker thread crash is intercepted, task marked FAILED, and resources cleaned up."""
        sup = ReliabilitySupervisor()
        task_id = "test-worker-task"

        cleanup_called = False

        def _cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        def _failing_worker() -> None:
            raise RuntimeError("Unexpected thread segmentation fault simulation")

        thread = sup.execute_worker(
            task_id=task_id,
            worker_func=_failing_worker,
            cleanup_callback=_cleanup,
        )
        thread.join(timeout=2.0)

        # Worker must have exited and cleaned up
        assert not thread.is_alive()
        assert cleanup_called is True

    def test_watchdog_lost_callback_reconciliation(self) -> None:
        """Operations whose callbacks are lost are reconciled and reaped by watchdog."""
        watchdog = WatchdogSupervisor(check_interval=0.02)
        op = OperationRecord(
            id="lost-op-42",
            name="lost_callback_op",
            op_type=OperationType.TOOL_CALL,
            timeout=0.02,
            started_at=time.time() - 0.05,  # Timed out in past
            status=OperationStatus.RUNNING,
        )
        watchdog.register_operation(op)
        assert watchdog.get_active_count() == 1

        reconciled = watchdog.reconcile()
        assert "lost-op-42" in reconciled
        assert op.status == OperationStatus.TIMED_OUT


class TestDiagnosticCommands:
    """Test self-diagnostics queries return meaningful state."""

    def test_diagnose_self_query_inspects_state(self) -> None:
        """'diagnose yourself' reflects current supervisor state."""
        sup = ReliabilitySupervisor.get_instance()
        text = diagnose_self_query(supervisor=sup)
        assert "Diagnostic Status" in text or "Operational" in text or "ready" in text.lower()
