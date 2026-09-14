"""Data models and contracts for the AVI Command Palette, Instant Commands, and App Launcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class CommandCategory(str, Enum):
    """Categorisation of command palette items."""

    APPLICATION = "Applications"
    COMMAND = "Commands"
    CAPABILITY = "Capabilities"
    FILE = "Files"
    TASK = "Tasks"
    SYSTEM = "System"
    ACTION = "Actions"


class PaletteState(str, Enum):
    """Authoritative lifecycle state model for the command palette UI."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    SEARCHING = "SEARCHING"
    SHOWING_RESULTS = "SHOWING_RESULTS"
    EXECUTING = "EXECUTING"
    ERROR = "ERROR"


@dataclass
class ActionResult:
    """Action that can be executed from a palette result (primary or secondary via Tab)."""

    id: str
    name: str
    description: str = ""
    action_type: str = "execute"  # "launch", "focus", "close", "execute", "calculate", "task", "agent"
    payload: Any = None


@dataclass
class PaletteResult:
    """Structured result displayed in the command palette."""

    id: str
    title: str
    subtitle: str
    icon: str  # Real XDG icon name or absolute path, NEVER an emoji
    category: CommandCategory
    score: float = 0.0
    action_type: str = "execute"
    payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    actions: list[ActionResult] = field(default_factory=list)

    @property
    def primary_action(self) -> ActionResult:
        """Return the default primary action for Enter key execution."""
        if self.actions:
            return self.actions[0]
        return ActionResult(
            id=f"{self.id}:default",
            name=self.title,
            description=self.subtitle,
            action_type=self.action_type,
            payload=self.payload,
        )


@dataclass
class CommandDefinition:
    """Registered command entry in the Command Registry."""

    id: str
    name: str
    description: str
    category: CommandCategory = CommandCategory.COMMAND
    icon: str = "system-run"
    aliases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    handler: Callable[..., Any] | None = None
    capability_name: str | None = None
    requires_argument: bool = False
    argument_hint: str = ""
    actions: list[ActionResult] = field(default_factory=list)
