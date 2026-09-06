"""Base tool abstractions and results for AVI."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Standardized result returned by all tools."""

    success: bool
    data: Any = field(default_factory=dict)
    error: str | None = None
    display_override: str | None = None

    def format_display(self) -> str:
        """Render the structured result into human-readable terminal text."""
        if self.display_override is not None:
            return self.display_override
        if not self.success:
            return f"Error: {self.error or 'Tool execution failed'}"
        return str(self.data)

    def to_dict(self) -> dict[str, Any]:
        """Serialize ToolResult into a JSON-compatible dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "display": self.format_display(),
        }


class BaseTool(ABC):
    """Abstract base class for all read-only tools."""

    name: str
    description: str
    safety_level: str = "read_only"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    def to_metadata(self) -> dict[str, Any]:
        """Export standardized metadata and JSON Schema for protocol discovery."""
        schema = getattr(self, "input_schema", None) or {"type": "object", "properties": {}}
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
            "safety_level": self.safety_level,
            "availability": True,
        }

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the read-only operation and return a structured ToolResult."""
        pass
