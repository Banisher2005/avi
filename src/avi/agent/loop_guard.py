"""Loop guard and invocation abstraction for the AVI Agent Runtime.

Provides bounded execution protection against:
- Identical capability calls with identical arguments
- Ping-pong alternating loops (A -> B -> A -> B)
- Excessive total tool calls or orchestration turns
- Repeated failures with no state change
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from avi.capabilities.models import CapabilityResult

logger = logging.getLogger("avi.agent.loop_guard")


@dataclass
class Invocation:
    """Standard internal representation of a capability invocation request."""

    capability_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    step_id: int = 1
    turn: int = 0
    invocation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def normalized_signature(self) -> str:
        """Return stable hash of capability name and canonicalized arguments."""
        try:
            norm_args = json.dumps(self.arguments, sort_keys=True, default=str)
        except Exception:
            norm_args = str(sorted(self.arguments.items()))
        raw = f"{self.capability_name}:{norm_args}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "capability_name": self.capability_name,
            "arguments": self.arguments,
            "reason": self.reason,
            "step_id": self.step_id,
        }


@dataclass
class LoopGuardConfig:
    """Configurable boundaries for agent orchestration loop protection."""

    max_identical_invocations: int = 2
    max_ping_pong_cycles: int = 2
    max_total_invocations: int = 10
    max_consecutive_failures: int = 2
    max_orchestration_turns: int = 8


@dataclass
class LoopDetectionResult:
    """Outcome of evaluating an invocation against loop detection invariants."""

    is_loop: bool
    reason: str | None = None
    loop_type: str | None = (
        None  # identical_call, ping_pong, excessive_calls, repeated_failure, max_turns
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_loop": self.is_loop,
            "reason": self.reason,
            "loop_type": self.loop_type,
        }


class LoopGuard:
    """Guards the agent orchestration loop from infinite recursion and cycling."""

    def __init__(self, config: LoopGuardConfig | None = None) -> None:
        self.config = config or LoopGuardConfig()
        self._invocations: list[Invocation] = []
        self._signatures: list[str] = []
        self._results: list[CapabilityResult] = []
        self._turn_count: int = 0
        self._consecutive_failures: int = 0
        self._attempted_strategies: list[str] = []
        self._failed_strategies: set[str] = set()

    @property
    def total_invocations(self) -> int:
        return len(self._invocations)

    @property
    def current_turns(self) -> int:
        return self._turn_count

    def increment_turn(self) -> None:
        """Advance the turn counter."""
        self._turn_count += 1

    def record_strategy(self, strategy_name: str, success: bool = False) -> None:
        """Record an attempted strategy and whether it succeeded."""
        if strategy_name:
            self._attempted_strategies.append(strategy_name)
            if not success:
                self._failed_strategies.add(strategy_name)
            else:
                self._failed_strategies.discard(strategy_name)

    def is_strategy_forbidden(self, strategy_name: str) -> bool:
        """Check if strategy has already failed and should not be repeated."""
        return bool(strategy_name and strategy_name in self._failed_strategies)

    def check_invocation(self, invocation: Invocation) -> LoopDetectionResult:
        """Check if an upcoming invocation would violate loop boundaries."""
        # 1. Total invocations boundary
        if len(self._invocations) >= self.config.max_total_invocations:
            msg = (
                f"LoopGuard: Maximum tool invocation limit reached ({self.config.max_total_invocations}). "
                "Halting orchestration safely to prevent infinite tool loops."
            )
            logger.warning(msg)
            return LoopDetectionResult(is_loop=True, reason=msg, loop_type="excessive_calls")

        # 2. Total orchestration turns boundary
        if self._turn_count >= self.config.max_orchestration_turns:
            msg = (
                f"LoopGuard: Maximum orchestration turns reached ({self.config.max_orchestration_turns}). "
                "Halting orchestration safely."
            )
            logger.warning(msg)
            return LoopDetectionResult(is_loop=True, reason=msg, loop_type="max_turns")

        # 3. Repeated failures boundary
        if self._consecutive_failures >= self.config.max_consecutive_failures:
            msg = (
                f"LoopGuard: {self._consecutive_failures} consecutive actions failed with no state change. "
                "Halting orchestration safely."
            )
            logger.warning(msg)
            return LoopDetectionResult(is_loop=True, reason=msg, loop_type="repeated_failure")

        sig = invocation.normalized_signature()

        # 4. Identical calls limit
        matching_identical = sum(1 for s in self._signatures if s == sig)
        if matching_identical >= self.config.max_identical_invocations:
            msg = (
                f"LoopGuard: Capability '{invocation.capability_name}' has already been invoked "
                f"{matching_identical} times with identical arguments. Halting to prevent repetition."
            )
            logger.warning(msg)
            return LoopDetectionResult(is_loop=True, reason=msg, loop_type="identical_call")

        # 5. Ping-pong cycle detection: A -> B -> A -> B
        if len(self._signatures) >= 3:
            recent_sigs = self._signatures + [sig]
            # Check for cycle of length 2 (e.g. A, B, A, B)
            if len(recent_sigs) >= 4:
                a1, b1, a2, b2 = recent_sigs[-4:]
                if a1 == a2 and b1 == b2 and a1 != b1:
                    cycle_count = 1
                    # Check if previous history has another cycle
                    if len(recent_sigs) >= 6:
                        a0, b0 = recent_sigs[-6:-4]
                        if a0 == a1 and b0 == b1:
                            cycle_count = 2

                    if cycle_count >= self.config.max_ping_pong_cycles:
                        msg = (
                            f"LoopGuard: Detected ping-pong alternating loop between actions "
                            f"({invocation.capability_name}). Halting orchestration safely."
                        )
                        logger.warning(msg)
                        return LoopDetectionResult(is_loop=True, reason=msg, loop_type="ping_pong")

        return LoopDetectionResult(is_loop=False)

    def record_invocation(self, invocation: Invocation) -> None:
        """Record an approved invocation into history."""
        self._invocations.append(invocation)
        self._signatures.append(invocation.normalized_signature())

    def record_and_check(
        self,
        capability_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> LoopDetectionResult:
        """Convenience method to inspect an invocation and record it if allowed."""
        inv = Invocation(
            capability_name=capability_name,
            arguments=arguments or {},
            turn=self._turn_count,
        )
        res = self.check_invocation(inv)
        if not res.is_loop:
            self.record_invocation(inv)
        return res

    def record_result(self, invocation: Invocation, result: CapabilityResult) -> None:
        """Record execution outcome and track failure momentum."""
        self._results.append(result)
        if not result.success:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

    def reset(self) -> None:
        """Reset state for a fresh task."""
        self._invocations.clear()
        self._signatures.clear()
        self._results.clear()
        self._turn_count = 0
        self._consecutive_failures = 0
        self._attempted_strategies.clear()
        self._failed_strategies.clear()
