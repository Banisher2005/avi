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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlanStep":
        status_val = d.get("status", "pending")
        try:
            status = StepStatus(status_val)
        except Exception:
            status = StepStatus.PENDING
        fc_val = d.get("failure_category")
        fc = None
        if fc_val:
            try:
                fc = FailureCategory(fc_val)
            except Exception:
                fc = None
        return cls(
            step_id=d.get("step_id", 0),
            capability_name=d.get("capability_name", ""),
            arguments=d.get("arguments", {}),
            description=d.get("description", ""),
            status=status,
            pipe_from_step=d.get("pipe_from_step"),
            pipe_arg_name=d.get("pipe_arg_name"),
            verified=d.get("verified", False),
            duration_ms=d.get("duration_ms", 0.0),
            artifact_path=d.get("artifact_path"),
            expected_outcome=d.get("expected_outcome", ""),
            fallback_capability=d.get("fallback_capability"),
            retry_count=d.get("retry_count", 0),
            failure_category=fc,
        )


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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Plan":
        steps = [PlanStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            plan_id=d.get("plan_id", str(uuid.uuid4())),
            user_goal=d.get("user_goal", ""),
            steps=steps,
            max_steps=d.get("max_steps", 5),
            requires_confirmation=d.get("requires_confirmation", False),
            confirmation_prompt=d.get("confirmation_prompt", ""),
            strategy_name=d.get("strategy_name", "primary"),
            attempted_strategies=d.get("attempted_strategies", []),
            context_data=d.get("context_data", {}),
        )


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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskState":
        return cls(
            task_id=d.get("task_id", str(uuid.uuid4())),
            goal=d.get("goal", ""),
            status=d.get("status", "pending"),
            current_step_index=d.get("current_step_index", 0),
            completed_steps=[PlanStep.from_dict(s) for s in d.get("completed_steps", [])],
            failed_steps=[PlanStep.from_dict(s) for s in d.get("failed_steps", [])],
            pending_steps=[PlanStep.from_dict(s) for s in d.get("pending_steps", [])],
            final_result=d.get("final_result"),
            retry_counts=d.get("retry_counts", {}),
            attempted_strategies=d.get("attempted_strategies", []),
            observation_history=d.get("observation_history", []),
            state_snapshots=d.get("state_snapshots", []),
        )


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


@dataclass
class GoalSegment:
    """A discrete milestone segment within a long-horizon user goal."""

    segment_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    expected_outcome: str = ""
    verification_condition: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, running, completed, failed, skipped
    plan: Plan | None = None
    completed_steps: list[PlanStep] = field(default_factory=list)
    attempted_strategies: list[str] = field(default_factory=list)
    result_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "title": self.title,
            "description": self.description,
            "expected_outcome": self.expected_outcome,
            "verification_condition": self.verification_condition,
            "status": self.status,
            "plan": self.plan.to_dict() if self.plan else None,
            "completed_steps": [s.to_dict() for s in self.completed_steps],
            "attempted_strategies": self.attempted_strategies,
            "result_data": self.result_data,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GoalSegment":
        plan_dict = d.get("plan")
        plan = Plan.from_dict(plan_dict) if plan_dict else None
        completed_steps = [PlanStep.from_dict(s) for s in d.get("completed_steps", [])]
        return cls(
            segment_id=d.get("segment_id", str(uuid.uuid4())[:8]),
            title=d.get("title", ""),
            description=d.get("description", ""),
            expected_outcome=d.get("expected_outcome", ""),
            verification_condition=d.get("verification_condition", {}),
            status=d.get("status", "pending"),
            plan=plan,
            completed_steps=completed_steps,
            attempted_strategies=d.get("attempted_strategies", []),
            result_data=d.get("result_data", {}),
            error=d.get("error"),
        )


@dataclass
class Goal:
    """A high-level, durable user objective composed of one or more milestone segments."""

    goal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_goal: str = ""
    normalized_goal: str = ""
    segments: list[GoalSegment] = field(default_factory=list)
    current_segment_index: int = 0
    status: str = "pending"  # pending, running, paused, paused_for_confirmation, completed, failed, cancelled
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    context_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def current_segment(self) -> GoalSegment | None:
        if 0 <= self.current_segment_index < len(self.segments):
            return self.segments[self.current_segment_index]
        return None

    @property
    def is_decomposed(self) -> bool:
        return len(self.segments) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "user_goal": self.user_goal,
            "normalized_goal": self.normalized_goal,
            "segments": [s.to_dict() for s in self.segments],
            "current_segment_index": self.current_segment_index,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "context_data": self.context_data,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Goal":
        segments = [GoalSegment.from_dict(s) for s in d.get("segments", [])]
        return cls(
            goal_id=d.get("goal_id", str(uuid.uuid4())),
            user_goal=d.get("user_goal", ""),
            normalized_goal=d.get("normalized_goal", ""),
            segments=segments,
            current_segment_index=d.get("current_segment_index", 0),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            completed_at=d.get("completed_at"),
            context_data=d.get("context_data", {}),
            metadata=d.get("metadata", {}),
        )

