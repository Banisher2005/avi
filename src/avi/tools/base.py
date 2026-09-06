"""Base tool abstractions and results for AVI."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


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


class BaseTool(ABC):
    """Abstract base class for all read-only tools."""

    name: str
    description: str
    safety_level: str = "read_only"

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the read-only operation and return a structured ToolResult."""
        pass
