"""Data models and abstractions for the AVI Agent Runtime."""

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from avi.capabilities.models import CapabilityResult, ExecutionStatus


class InputSource(str, Enum):
    """Input modality or trigger source for assistant requests."""

    TEXT = "text"
    VOICE = "voice"
    HOTKEY = "hotkey"
    API = "api"
    UI = "ui"


@dataclass
class AssistantInput:
    """Unified user input payload received by the assistant."""

    text: str
    source: InputSource = InputSource.TEXT
    context: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    confirmed: bool = False
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class StepStatus(str, Enum):
    """Execution status for an individual plan step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CONFIRMATION_REQUIRED = "confirmation_required"


class FailureCategory(str, Enum):
    """Categorization of step execution failures for adaptive recovery."""

    TEMPORARY_LOADING = "temporary_loading"
    STALE_STATE = "stale_state"
    WRONG_ASSUMPTION = "wrong_assumption"
    NAVIGATION_FAILURE = "navigation_failure"
    CONFIRMATION_REQUIRED = "confirmation_required"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    REPEATED_FAILURE = "repeated_failure"
    EXECUTION_ERROR = "execution_error"


@dataclass
class PlanStep:
    """A discrete capability invocation step in an execution plan."""

    step_id: int
    capability_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    result: CapabilityResult | None = None
    pipe_from_step: int | None = None
    pipe_arg_name: str = ""
    verified: bool | None = None
    duration_ms: float = 0.0
    artifact_path: str | None = None
    expected_outcome: str = ""
    verification_condition: dict[str, Any] = field(default_factory=dict)
    fallback_capability: str | None = None
    fallback_arguments: dict[str, Any] | None = None
    retry_count: int = 0
    failure_category: FailureCategory | None = None
    requires_observation_before: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_name": self.capability_name,
            "arguments": self.arguments,
            "description": self.description,
            "status": self.status.value,
            "pipe_from_step": self.pipe_from_step,
            "pipe_arg_name": self.pipe_arg_name,
            "verified": self.verified,
            "duration_ms": self.duration_ms,
            "artifact_path": self.artifact_path,
            "expected_outcome": self.expected_outcome,
            "fallback_capability": self.fallback_capability,
            "retry_count": self.retry_count,
            "failure_category": self.failure_category.value if self.failure_category else None,
        }


@dataclass
class Plan:
    """A bounded sequence of capability steps to fulfill a user goal."""

    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    max_steps: int = 5
    requires_confirmation: bool = False
    confirmation_prompt: str = ""
    strategy_name: str = "primary"
    attempted_strategies: list[str] = field(default_factory=list)
    context_data: dict[str, Any] = field(default_factory=dict)

    @property
    def goal(self) -> str:
        return self.user_goal

    @goal.setter
    def goal(self, val: str) -> None:
        self.user_goal = val

    @property
    def is_single_step(self) -> bool:
        return len(self.steps) == 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "user_goal": self.user_goal,
            "steps": [s.to_dict() for s in self.steps],
            "max_steps": self.max_steps,
            "requires_confirmation": self.requires_confirmation,
            "strategy_name": self.strategy_name,
            "attempted_strategies": self.attempted_strategies,
        }


@dataclass
class TaskState:
    """Live state tracking for multi-step agent task execution."""

    goal: str
    status: str = "pending"  # pending, running, success, failed, partial_success
    current_step_index: int = 0
    completed_steps: list[PlanStep] = field(default_factory=list)
    failed_steps: list[PlanStep] = field(default_factory=list)
    pending_steps: list[PlanStep] = field(default_factory=list)
    final_result: Any = None
    retry_counts: dict[int, int] = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attempted_strategies: list[dict[str, Any]] = field(default_factory=list)
    observation_history: list[dict[str, Any]] = field(default_factory=list)
    state_snapshots: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "current_step_index": self.current_step_index,
            "completed_steps": [s.to_dict() for s in self.completed_steps],
            "failed_steps": [s.to_dict() for s in self.failed_steps],
            "pending_steps": [s.to_dict() for s in self.pending_steps],
            "final_result": self.final_result,
            "attempted_strategies": self.attempted_strategies,
            "observation_history": self.observation_history,
            "state_snapshots": self.state_snapshots,
        }


@dataclass
class PlanExecutionResult:
    """Consolidated outcome of executing an agent plan."""

    success: bool
    status: ExecutionStatus
    plan: Plan
    completed_steps: list[PlanStep] = field(default_factory=list)
    final_message: str = ""
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    confirmation_required: bool = False
    pending_step: PlanStep | None = None
    task_state: TaskState | None = None
    planning_duration_ms: float = 0.0
    action_duration_ms: float = 0.0
    verification_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "plan_id": self.plan.plan_id,
            "completed_steps": [s.to_dict() for s in self.completed_steps],
            "final_message": self.final_message,
            "error": self.error,
            "data": self.data,
            "confirmation_required": self.confirmation_required,
            "task_state": self.task_state.to_dict() if self.task_state else None,
            "planning_duration_ms": self.planning_duration_ms,
            "action_duration_ms": self.action_duration_ms,
            "verification_duration_ms": self.verification_duration_ms,
        }
