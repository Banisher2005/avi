"""Event-driven progress reporting and streaming updates for agent orchestration."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("avi.agent.events")


class ProgressEventType(str, Enum):
    """Standard lifecycle event types emitted by AgentOrchestrator."""

    TASK_STARTED = "task_started"
    PLANNING = "planning"
    PLAN_CREATED = "plan_created"
    CAPABILITY_SELECTED = "capability_selected"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETED = "verification_completed"
    STATE_CHANGE_VERIFIED = "state_change_verified"
    OBSERVATION_CAPTURED = "observation_captured"
    FAILURE_DIAGNOSED = "failure_diagnosed"
    RECOVERY_ATTEMPTED = "recovery_attempted"
    REPLANNING = "replanning"
    PLAN_ADAPTED = "plan_adapted"
    CONFIRMATION_REQUIRED = "confirmation_required"
    PAUSED_FOR_CONFIRMATION = "paused_for_confirmation"
    GOAL_CREATED = "goal_created"
    GOAL_RESUMED = "goal_resumed"
    GOAL_RECONCILED = "goal_reconciled"
    GOAL_COMPLETED = "goal_completed"
    GOAL_FAILED = "goal_failed"
    STATE_PERSISTED = "state_persisted"
    STATE_RESTORED = "state_restored"
    EXPERIENCE_RECORDED = "experience_recorded"
    STRATEGY_REUSED = "strategy_reused"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


@dataclass
class ProgressEvent:
    """An event emitted during agent orchestration for UI and CLI progress display."""

    event_type: ProgressEventType
    task_id: str
    message: str
    timestamp: float = field(default_factory=time.time)
    step_index: int | None = None
    capability_name: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "message": self.message,
            "timestamp": self.timestamp,
            "step_index": self.step_index,
            "capability_name": self.capability_name,
            "data": self.data,
        }


ProgressCallback = Callable[[ProgressEvent], None]


class EventDispatcher:
    """Safe, multicast event dispatcher for progress listeners."""

    def __init__(self) -> None:
        self._subscribers: list[ProgressCallback] = []

    def subscribe(self, callback: ProgressCallback) -> None:
        """Register a subscriber callback."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: ProgressCallback) -> None:
        """Remove a subscriber callback."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def emit(self, event: ProgressEvent) -> None:
        """Dispatch event to all registered subscribers, isolating exceptions."""
        for sub in list(self._subscribers):
            try:
                sub(event)
            except Exception as e:
                logger.warning(f"Error in progress subscriber: {e}", exc_info=True)
