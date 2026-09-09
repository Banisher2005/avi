"""Task context and state machine representation for agent orchestration."""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any
import uuid

from avi.memory.models import Memory


class TaskStatus(str, Enum):
    """Execution status for agent tasks."""
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    PAUSED_FOR_CONFIRMATION = "paused_for_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class StepRecord:
    """Record of an individual step in an execution plan."""
    step_index: int
    capability_name: str
    args: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    status: str = "pending"  # pending, executing, completed, failed, skipped
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    observation: Any = None
    verification_result: bool | None = None
    verification_message: str | None = None
    error: str | None = None
    requires_confirmation: bool = False
    confirmed: bool = False
    duration_ms: float = 0.0
    retry_count: int = 0
    artifact_path: str | None = None
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "capability_name": self.capability_name,
            "args": self.args,
            "input_data": self.input_data or self.args,
            "output_data": self.output_data,
            "description": self.description,
            "status": self.status,
            "observation": str(self.observation) if self.observation is not None else None,
            "verification_result": self.verification_result,
            "verification_message": self.verification_message,
            "error": self.error,
            "requires_confirmation": self.requires_confirmation,
            "confirmed": self.confirmed,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
            "artifact_path": self.artifact_path,
            "artifacts": self.artifacts,
        }


@dataclass
class OrchestrationContext:
    """Unified, serializable execution context for agent tasks."""
    user_prompt: str
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    status: TaskStatus = TaskStatus.IDLE
    memories: list[Memory] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    current_step_index: int = 0
    replan_count: int = 0
    max_replans: int = 1
    retry_counts: dict[int, int] = field(default_factory=dict)
    error_details: list[str] = field(default_factory=list)
    final_response: str = ""
    start_time: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def current_step(self) -> StepRecord | None:
        """Return the current step being executed, or None if out of bounds."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def completed_steps(self) -> list[StepRecord]:
        """Return all successfully completed steps."""
        return [s for s in self.steps if s.status == "completed"]

    def failed_steps(self) -> list[StepRecord]:
        """Return all failed steps."""
        return [s for s in self.steps if s.status == "failed"]

    def is_terminal(self) -> bool:
        """Check if task is in a terminal state."""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.PAUSED_FOR_CONFIRMATION,
        )

    def record_error(self, message: str) -> None:
        """Record an error message in context history."""
        clean_msg = message.strip()
        if clean_msg and clean_msg not in self.error_details:
            self.error_details.append(clean_msg)

    def to_dict(self) -> dict[str, Any]:
        """Serialize complete context to dictionary for observability and logging."""
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "user_prompt": self.user_prompt,
            "status": self.status.value,
            "current_step_index": self.current_step_index,
            "total_steps": len(self.steps),
            "replan_count": self.replan_count,
            "max_replans": self.max_replans,
            "steps": [s.to_dict() for s in self.steps],
            "memories_count": len(self.memories),
            "error_details": self.error_details,
            "final_response": self.final_response,
            "elapsed_seconds": round(time.time() - self.start_time, 3),
            "metadata": self.metadata,
        }
