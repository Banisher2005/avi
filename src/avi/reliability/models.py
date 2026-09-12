"""Data models for reliability supervision, operation tracking, timeouts, and failure classification."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OperationStatus(str, Enum):
    """Lifecycle statuses for supervised operations."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    WAITING = "waiting"

    @property
    def is_terminal(self) -> bool:
        """Whether this status is terminal."""
        return self in (
            OperationStatus.COMPLETED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.TIMED_OUT,
        )


class OperationType(str, Enum):
    """Classification of operations undergoing supervision."""

    PROVIDER_CALL = "provider_call"
    TOOL_CALL = "tool_call"
    AGENT_STEP = "agent_step"
    FILESYSTEM_OP = "filesystem_op"
    BROWSER_OP = "browser_op"
    SUBPROCESS = "subprocess"
    VERIFICATION = "verification"
    BACKGROUND_WORKER = "background_worker"


class FailureCategory(str, Enum):
    """Structured error categories replacing brittle string matching."""

    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_CANCELLED = "PROVIDER_CANCELLED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    TOOL_INVALID_ARGUMENT = "TOOL_INVALID_ARGUMENT"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    PLANNER_FAILURE = "PLANNER_FAILURE"
    MALFORMED_MODEL_OUTPUT = "MALFORMED_MODEL_OUTPUT"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    NO_PROGRESS = "NO_PROGRESS"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    USER_CANCELLED = "USER_CANCELLED"
    USER_PAUSED = "USER_PAUSED"


@dataclass
class TimeoutConfig:
    """Configurable layered timeout hierarchy."""

    fast_path: float = 0.05
    fast_deterministic: float = 3.0
    default_tool: float = 20.0
    filesystem_metadata: float = 5.0
    filesystem_search: float = 15.0
    application_launch: float = 60.0
    browser_navigation: float = 30.0
    provider_call: float = 30.0
    provider_initial_response: float = 20.0
    provider_total_generation: float = 60.0
    agent_step: float = 60.0
    background_task: float = 300.0
    task_total: float = 180.0
    watchdog_check_interval: float = 0.5

    def get_tool_timeout(self, tool_name: str) -> float:
        """Get timeout for a specific tool by name."""
        name_lower = tool_name.lower()
        if "search" in name_lower or "duplicates" in name_lower or "largest" in name_lower:
            return self.filesystem_search
        if "browser" in name_lower or "url" in name_lower or "web" in name_lower:
            return self.browser_navigation
        if "app" in name_lower:
            return self.application_launch
        return self.default_tool

    def get_timeout_for_operation(self, op_type: OperationType, name: str = "") -> float:
        """Get appropriate layered timeout for operation type and name."""
        name_lower = name.lower()
        if "calc" in name_lower or "math" in name_lower or "ram" in name_lower or "time" in name_lower:
            return self.fast_deterministic
        if op_type == OperationType.PROVIDER_CALL:
            return self.provider_call
        if op_type == OperationType.BROWSER_OP or "browser" in name_lower:
            return self.browser_navigation
        if "search" in name_lower or "duplicates" in name_lower or "largest" in name_lower:
            return self.filesystem_search
        if op_type == OperationType.FILESYSTEM_OP or "filesystem" in name_lower:
            return self.filesystem_metadata
        if op_type == OperationType.SUBPROCESS or "app" in name_lower:
            return self.application_launch
        if op_type == OperationType.AGENT_STEP:
            return self.agent_step
        if op_type in (OperationType.BACKGROUND_WORKER, OperationType.WORKER):
            return self.background_task
        return self.agent_step


@dataclass
class OperationRecord:
    """Structured record tracking a single supervised operation."""

    operation_id: str = field(default_factory=lambda: f"op_{uuid.uuid4().hex[:10]}")
    task_id: str = ""
    operation_type: OperationType = OperationType.TOOL_CALL
    name: str = ""
    started_at: float = field(default_factory=time.time)
    timeout: float = 30.0
    cancellation_token: Any = None
    status: OperationStatus = OperationStatus.PENDING
    completed_at: float | None = None
    duration_ms: float = 0.0
    result: Any = None
    error: str | None = None
    failure_category: FailureCategory | None = None
    user_message: str | None = None
    retry_count: int = 0
    max_retries: int = 0

    @property
    def is_expired(self) -> bool:
        """Check if operation has exceeded its allocated timeout."""
        if self.status.is_terminal:
            return False
        return (time.time() - self.started_at) > self.timeout

    def mark_completed(self, result: Any, user_message: str = "") -> None:
        """Mark operation completed successfully."""
        self.completed_at = time.time()
        self.duration_ms = (self.completed_at - self.started_at) * 1000.0
        self.status = OperationStatus.COMPLETED
        self.result = result
        self.user_message = user_message

    def mark_failed(
        self,
        error: str,
        category: FailureCategory = FailureCategory.TOOL_EXECUTION_FAILED,
        user_message: str = "",
    ) -> None:
        """Mark operation failed with categorized reason."""
        self.completed_at = time.time()
        self.duration_ms = (self.completed_at - self.started_at) * 1000.0
        self.status = OperationStatus.FAILED
        self.error = error
        self.failure_category = category
        self.user_message = user_message or error

    def mark_timed_out(self, user_message: str = "") -> None:
        """Mark operation timed out."""
        self.completed_at = time.time()
        self.duration_ms = (self.completed_at - self.started_at) * 1000.0
        self.status = OperationStatus.TIMED_OUT
        self.error = f"Operation '{self.name}' timed out after {self.timeout:.1f}s."
        if self.operation_type == OperationType.PROVIDER_CALL:
            self.failure_category = FailureCategory.PROVIDER_TIMEOUT
            self.user_message = user_message or "The local AI model timed out. Your other AVI commands are still available."
        elif self.operation_type == OperationType.BROWSER_OP:
            self.failure_category = FailureCategory.TOOL_TIMEOUT
            self.user_message = user_message or "Browser navigation timed out."
        elif "search" in self.name.lower():
            self.failure_category = FailureCategory.TOOL_TIMEOUT
            self.user_message = user_message or "File search took too long."
        else:
            self.failure_category = FailureCategory.TOOL_TIMEOUT
            self.user_message = user_message or f"Action '{self.name}' took too long and was stopped."

    def mark_cancelled(self, user_message: str = "") -> None:
        """Mark operation cancelled by user or supervisor."""
        self.completed_at = time.time()
        self.duration_ms = (self.completed_at - self.started_at) * 1000.0
        self.status = OperationStatus.CANCELLED
        self.error = f"Operation '{self.name}' was cancelled."
        self.failure_category = FailureCategory.USER_CANCELLED
        self.user_message = user_message or "Operation cancelled by user."

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary for telemetry/diagnostics."""
        return {
            "operation_id": self.operation_id,
            "task_id": self.task_id,
            "operation_type": self.operation_type.value,
            "name": self.name,
            "started_at": self.started_at,
            "timeout": self.timeout,
            "status": self.status.value,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "failure_category": self.failure_category.value if self.failure_category else None,
            "user_message": self.user_message,
            "retry_count": self.retry_count,
        }


@dataclass
class SupervisedResult:
    """Standardized outcome for every supervised operation."""

    success: bool
    status: OperationStatus
    data: Any = None
    error: str | None = None
    failure_category: FailureCategory | None = None
    user_message: str = ""
    duration_ms: float = 0.0
    operation_record: OperationRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "data": self.data,
            "error": self.error,
            "failure_category": self.failure_category.value if self.failure_category else None,
            "user_message": self.user_message,
            "duration_ms": self.duration_ms,
            "operation": self.operation_record.to_dict() if self.operation_record else None,
        }
