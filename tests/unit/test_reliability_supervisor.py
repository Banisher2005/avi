"""Unit tests for AVI Reliability Supervisor, Circuit Breakers, Timeouts, and Watchdog."""

import time

from avi.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitBreakerState,
)
from avi.reliability.health import HealthMonitor, diagnose_self_query, startup_self_check
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


class TestTimeoutConfig:
    """Test layered timeout resolution."""

    def test_default_timeouts(self) -> None:
        cfg = TimeoutConfig()
        assert cfg.fast_path == 0.05
        assert cfg.provider_call == 30.0
        assert cfg.agent_step == 60.0
        assert cfg.background_task == 300.0

    def test_get_tool_timeout(self) -> None:
        cfg = TimeoutConfig()
        assert cfg.get_tool_timeout("filesystem.search") == 15.0
        assert cfg.get_tool_timeout("browser.navigate") == 30.0
        assert cfg.get_tool_timeout("desktop.open_app") == 60.0
        assert cfg.get_tool_timeout("custom.tool") == 20.0


class TestCircuitBreaker:
    """Test provider circuit breaker state transitions and isolation."""

    def test_initial_state_closed(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=3, recovery_cooldown=1.0)
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.can_execute() is True

    def test_trips_to_open_after_threshold_failures(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=3, recovery_cooldown=1.0)
        cb.record_failure(ValueError("err 1"))
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.consecutive_failures == 1

        cb.record_failure(ValueError("err 2"))
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.consecutive_failures == 2

        cb.record_failure(ValueError("err 3"))
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.can_execute() is False

    def test_cools_down_to_half_open(self) -> None:
        cb = CircuitBreaker("test-provider", failure_threshold=2, recovery_cooldown=0.05)
        cb.record_failure(Exception("e1"))
        cb.record_failure(Exception("e2"))
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.can_execute() is False

        time.sleep(0.06)
        # After cooldown elapsed, can_execute should allow a test probe and state becomes HALF_OPEN
        assert cb.can_execute() is True
        assert cb.state == CircuitBreakerState.HALF_OPEN

        # If probe succeeds, resets to CLOSED
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.consecutive_failures == 0

    def test_registry_provider_isolation(self) -> None:
        reg = CircuitBreakerRegistry(default_threshold=2, default_cooldown=1.0)
        reg.record_failure("ollama", Exception("offline"))
        reg.record_failure("ollama", Exception("offline"))

        assert reg.can_execute("ollama") is False
        # Other providers remain fully operational
        assert reg.can_execute("antigravity") is True
        assert reg.can_execute("calculator") is True


class TestWatchdogSupervisor:
    """Test background watchdog tracking and state reconciliation."""

    def test_tracks_and_reconciles_operations(self) -> None:
        watchdog = WatchdogSupervisor(check_interval=0.05)
        op = OperationRecord(
            id="op-test-1",
            name="test-operation",
            op_type=OperationType.TOOL_CALL,
            timeout=0.05,
            started_at=time.time() - 0.1,  # Already timed out
            status=OperationStatus.RUNNING,
        )
        watchdog.register_operation(op)
        assert watchdog.get_active_count() == 1

        # Reconcile should detect the timeout and invoke callback
        timed_out_ops: list[str] = []
        watchdog.reconcile(on_timeout=lambda timed_op: timed_out_ops.append(timed_op.id))

        assert "op-test-1" in timed_out_ops
        assert op.status == OperationStatus.TIMED_OUT


class TestReliabilitySupervisor:
    """Test full ReliabilitySupervisor execution envelope and retry controls."""

    def test_execute_tool_success(self) -> None:
        sup = ReliabilitySupervisor(timeouts=TimeoutConfig(default_tool=1.0))
        res: SupervisedResult = sup.execute_tool("my_tool", lambda: 42)
        assert res.is_success is True
        assert res.value == 42
        assert res.status == OperationStatus.COMPLETED

    def test_execute_tool_timeout(self) -> None:
        sup = ReliabilitySupervisor(timeouts=TimeoutConfig(default_tool=0.05))

        def _slow_tool() -> str:
            time.sleep(0.15)
            return "done"

        res: SupervisedResult = sup.execute_tool("slow_tool", _slow_tool)
        assert res.is_success is False
        assert res.status == OperationStatus.TIMED_OUT
        assert res.failure_category == FailureCategory.TOOL_TIMEOUT

    def test_execute_with_retry_on_transient_failure(self) -> None:
        sup = ReliabilitySupervisor()
        attempts = 0

        def _flaky_call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("Temporary glitch")
            return "success!"

        res: SupervisedResult = sup.execute_with_retry(
            func=_flaky_call,
            op_type=OperationType.PROVIDER_CALL,
            name="flaky_call",
            max_retries=3,
            initial_backoff=0.01,
            backoff_factor=1.5,
        )
        assert res.is_success is True
        assert res.value == "success!"
        assert attempts == 3

    def test_diagnose_self(self) -> None:
        sup = ReliabilitySupervisor()
        diag = sup.diagnose_self()
        assert "system_health" in diag
        assert "circuit_breakers" in diag
        assert "active_operations" in diag


class TestHealthDiagnostics:
    """Test health check and self-diagnostic queries."""

    def test_startup_self_check(self) -> None:
        ready, msg = startup_self_check()
        assert isinstance(ready, bool)
        assert isinstance(msg, str)

    def test_diagnose_self_query(self) -> None:
        ans = diagnose_self_query(query="diagnose yourself")
        assert "Diagnostic Status" in ans or "Operational" in ans or "ready" in ans.lower()

    def test_health_monitor_report(self) -> None:
        monitor = HealthMonitor()
        report = monitor.check_health()
        assert "runtime" in report.subsystems
        assert "database" in report.subsystems
        assert "capabilities" in report.subsystems
        cli_output = report.format_cli()
        assert "AVI System Diagnostics" in cli_output
