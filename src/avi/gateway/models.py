"""Normalized Gateway models for protocol-agnostic communication."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GatewayToolDefinition:
    """Standardized representation of a discovered tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    safety_level: str = "read_only"
    availability: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "safety_level": self.safety_level,
            "availability": self.availability,
        }


@dataclass
class GatewayConfirmation:
    """Pending confirmation state for a mutating operation."""

    token: str
    command: str
    reason: str
    created_at: float
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "confirmation_required",
            "operation": self.command,
            "reason": self.reason,
            "confirmation_token": self.token,
            "expires_at": self.expires_at,
            "message": "This command can modify system state. Resubmit with confirmed=true and confirmation_token.",
        }


@dataclass
class GatewayExecutionResponse:
    """Execution summary returned to external AI clients."""

    status: str  # "executed", "blocked", "confirmation_required", "error"
    command: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    display: str = ""
    duration_ms: float = 0.0
    reason: str | None = None
    error: str | None = None
    confirmation_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "status": self.status,
            "command": self.command,
        }
        if self.exit_code is not None:
            res["exit_code"] = self.exit_code
        if self.stdout:
            res["stdout"] = self.stdout
        if self.stderr:
            res["stderr"] = self.stderr
        if self.display:
            res["display"] = self.display
        if self.duration_ms:
            res["duration_ms"] = round(self.duration_ms, 3)
        if self.reason:
            res["reason"] = self.reason
        if self.error:
            res["error"] = self.error
        if self.confirmation_token:
            res["confirmation_token"] = self.confirmation_token
        return res
