"""Data models for AVI skills and structured action results."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillAction:
    """Definition of an individual action provided by a skill."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    is_safe: bool = True
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "is_safe": self.is_safe,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass
class SkillResult:
    """Structured result returned by a skill execution."""

    success: bool
    action: str
    target: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    data: Any = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "target": self.target,
            "details": self.details,
            "error": self.error,
            "data": self.data,
            "message": self.message,
        }

    def format_summary(self) -> str:
        """Format a human-readable display line."""
        if self.message:
            return self.message
        if self.success:
            tgt = f" on {self.target}" if self.target else ""
            return f"Action '{self.action}'{tgt} completed successfully."
        return f"Action '{self.action}' failed: {self.error or 'Unknown error'}"
