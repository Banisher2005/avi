"""Reliability supervisor, timeout controls, circuit breakers, and health diagnostics."""

from avi.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitBreakerState,
)
from avi.reliability.health import (
    HealthMonitor,
    HealthReport,
    diagnose_self_query,
    run_doctor,
    startup_self_check,
)
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

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitBreakerState",
    "FailureCategory",
    "HealthMonitor",
    "HealthReport",
    "OperationRecord",
    "OperationStatus",
    "OperationType",
    "ReliabilitySupervisor",
    "SupervisedResult",
    "TimeoutConfig",
    "WatchdogSupervisor",
    "diagnose_self_query",
    "run_doctor",
    "startup_self_check",
]
