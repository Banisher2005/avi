"""Data models for the AVI SQLite persistence layer."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PreferenceRecord:
    """A user preference or configuration setting."""

    key: str
    value: Any
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at,
        }


@dataclass
class MemoryRecord:
    """An explicit memory or learned preference in long-term storage."""

    id: str
    content: str
    category: str = "general"
    created_at: str = field(default_factory=utc_now_iso)
    last_used_at: str = field(default_factory=utc_now_iso)
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class AliasRecord:
    """A user-defined shortcut or application/directory alias."""

    alias: str
    target: str
    category: str = "general"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "target": self.target,
            "category": self.category,
            "created_at": self.created_at,
        }


@dataclass
class TaskRecord:
    """A recorded user goal / multi-step task execution outcome."""

    task_id: str
    goal: str
    status: str
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    plan_data: dict[str, Any] = field(default_factory=dict)
    result_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "plan_data": self.plan_data,
            "result_data": self.result_data,
        }


@dataclass
class ActionRecord:
    """An individual action invocation within a task or standalone execution."""

    action_id: str
    action_name: str
    target: str = ""
    task_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    duration_ms: float = 0.0
    executed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "task_id": self.task_id,
            "action_name": self.action_name,
            "target": self.target,
            "arguments": self.arguments,
            "success": self.success,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "executed_at": self.executed_at,
        }


@dataclass
class DurableTaskRecord:
    """A durable, versioned task state record capable of surviving process restarts."""

    task_id: str
    goal: str = ""
    status: str = "pending"
    normalized_goal: str = ""
    strategy_name: str = "primary"
    session_id: str = ""
    schema_version: int = 1
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    user_goal: str | None = None
    version: int | None = None
    plan_data: dict[str, Any] | None = None
    total_steps: int | None = None
    current_step_index: int | None = None
    environment_snapshot: dict[str, Any] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.user_goal and not self.goal:
            self.goal = self.user_goal
        self.user_goal = self.goal
        if self.version is not None:
            self.schema_version = self.version
        self.version = self.schema_version
        if self.plan_data is not None and not self.data:
            self.data = self.plan_data
        self.plan_data = self.data

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "normalized_goal": self.normalized_goal,
            "strategy_name": self.strategy_name,
            "session_id": self.session_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "data": self.data,
        }


@dataclass
class ExperienceRecord:
    """Execution experience memory connecting goal patterns to strategy outcomes."""

    experience_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal_pattern: str = ""
    normalized_goal: str = ""
    strategy_name: str = "primary"
    capability_used: str = ""
    success: bool = True
    failure_category: str | None = None
    working_recovery: str | None = None
    context_tags: list[str] = field(default_factory=list)
    verification_result: bool | None = None
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "goal_pattern": self.goal_pattern,
            "normalized_goal": self.normalized_goal,
            "strategy_name": self.strategy_name,
            "capability_used": self.capability_used,
            "success": self.success,
            "failure_category": self.failure_category,
            "working_recovery": self.working_recovery,
            "context_tags": self.context_tags,
            "verification_result": self.verification_result,
            "created_at": self.created_at,
        }


