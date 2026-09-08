"""Data models for the AVI SQLite persistence layer."""

import json
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
