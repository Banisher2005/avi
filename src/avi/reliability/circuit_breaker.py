"""Circuit breaker pattern implementation for isolating failing providers and external dependencies."""

import logging
import time
from enum import Enum
from typing import Any

logger = logging.getLogger("avi.reliability.circuit_breaker")


class CircuitBreakerState(str, Enum):
    """Lifecycle states for a circuit breaker."""

    CLOSED = "closed"        # Normal operation: requests pass through
    OPEN = "open"            # Failing: requests blocked immediately to prevent cascading hangs
    HALF_OPEN = "half_open"  # Probing: allowing single test request after cooldown


class CircuitBreaker:
    """Isolates failing external services to prevent repeated blocking and UI hangs."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_cooldown: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_cooldown = max(0.001, recovery_cooldown)
        self.state = CircuitBreakerState.CLOSED
        self.consecutive_failures = 0
        self.total_failures = 0
        self.total_successes = 0
        self.opened_at: float | None = None
        self.last_failure_reason: str | None = None
        self.last_failure_time: float | None = None

    def allow_request(self) -> bool:
        """Check if request is permitted through the circuit breaker."""
        now = time.time()
        if self.state == CircuitBreakerState.CLOSED:
            return True

        if self.state == CircuitBreakerState.OPEN:
            if self.opened_at and (now - self.opened_at) >= self.recovery_cooldown:
                logger.info(
                    "Circuit breaker '%s' entering HALF_OPEN probe state after %.1fs cooldown.",
                    self.name,
                    self.recovery_cooldown,
                )
                self.state = CircuitBreakerState.HALF_OPEN
                return True
            return False

        if self.state == CircuitBreakerState.HALF_OPEN:
            # Allow trial probe
            return True

        return True

    def can_execute(self) -> bool:
        """Convenience alias for allow_request()."""
        return self.allow_request()

    def record_success(self) -> None:
        """Record successful operation, resetting failure counter and closing circuit."""
        self.total_successes += 1
        if self.state in (CircuitBreakerState.HALF_OPEN, CircuitBreakerState.OPEN):
            logger.info("Circuit breaker '%s' recovered and transitioning to CLOSED.", self.name)
        self.state = CircuitBreakerState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self, error: str = "") -> None:
        """Record failed operation, tripping circuit breaker if threshold is exceeded."""
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        self.last_failure_reason = error

        if self.state == CircuitBreakerState.HALF_OPEN:
            # Probe failed; return to OPEN immediately
            logger.warning(
                "Circuit breaker '%s' probe failed. Returning to OPEN for %.1fs.",
                self.name,
                self.recovery_cooldown,
            )
            self.state = CircuitBreakerState.OPEN
            self.opened_at = time.time()
            return

        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            self.opened_at = time.time()
            logger.warning(
                "Circuit breaker '%s' TRIPPED to OPEN after %d consecutive failures. Cooldown: %.1fs. Error: %s",
                self.name,
                self.consecutive_failures,
                self.recovery_cooldown,
                error,
            )

    def is_open(self) -> bool:
        """Check if circuit is currently open and blocking requests."""
        if self.state == CircuitBreakerState.OPEN:
            now = time.time()
            if self.opened_at and (now - self.opened_at) >= self.recovery_cooldown:
                # Cooldown expired, will transition on next allow_request
                return False
            return True
        return False

    def reset(self) -> None:
        """Manually reset the circuit breaker to clean closed state."""
        self.state = CircuitBreakerState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None
        self.last_failure_reason = None

    def force_open(self, reason: str = "Manually tripped") -> None:
        """Manually force circuit breaker to OPEN state."""
        self.state = CircuitBreakerState.OPEN
        self.opened_at = time.time()
        self.last_failure_reason = reason

    def get_status(self) -> dict[str, Any]:
        """Get telemetry status dictionary."""
        cooldown_remaining = 0.0
        if self.state == CircuitBreakerState.OPEN and self.opened_at:
            cooldown_remaining = max(0.0, self.recovery_cooldown - (time.time() - self.opened_at))

        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "cooldown_remaining_sec": round(cooldown_remaining, 1),
            "last_failure_reason": self.last_failure_reason,
        }


class CircuitBreakerRegistry:
    """Registry maintaining independent circuit breakers for services and providers."""

    def __init__(self, default_threshold: int = 3, default_cooldown: float = 30.0) -> None:
        self.default_threshold = default_threshold
        self.default_cooldown = default_cooldown
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        name: str,
        failure_threshold: int | None = None,
        recovery_cooldown: float | None = None,
    ) -> CircuitBreaker:
        """Get existing circuit breaker or create a new configured instance."""
        clean_name = str(name).strip().lower()
        if clean_name not in self._breakers:
            self._breakers[clean_name] = CircuitBreaker(
                name=clean_name,
                failure_threshold=failure_threshold or self.default_threshold,
                recovery_cooldown=recovery_cooldown or self.default_cooldown,
            )
        return self._breakers[clean_name]

    def can_execute(self, name: str) -> bool:
        """Check if calls to named service are permitted."""
        return self.get_or_create(name).can_execute()

    def record_failure(self, name: str, reason: Any = None) -> None:
        """Record failure for named service."""
        self.get_or_create(name).record_failure(reason)

    def record_success(self, name: str) -> None:
        """Record success for named service."""
        self.get_or_create(name).record_success()

    def reset_all(self) -> None:
        """Reset all registered circuit breakers."""
        for cb in self._breakers.values():
            cb.reset()

    def get_all_statuses(self) -> dict[str, dict[str, Any]]:
        """Get telemetry statuses for all circuit breakers."""
        return {k: cb.get_status() for k, cb in self._breakers.items()}
