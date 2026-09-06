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

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_name": self.capability_name,
            "arguments": self.arguments,
            "description": self.description,
            "status": self.status.value,
            "pipe_from_step": self.pipe_from_step,
            "pipe_arg_name": self.pipe_arg_name,
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
        }
