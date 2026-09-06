"""Base models and interface for native assistant actions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from avi.safety.models import ActionCategory


@dataclass(frozen=True)
class ActionResult:
    """Outcome of executing a native assistant action."""

    success: bool
    message: str
    data: Any = None


class BaseAction(ABC):
    """Abstract base class for all native assistant actions."""

    name: str = "action"
    category: ActionCategory = ActionCategory.LOW_RISK_ACTION
    requires_confirmation: bool = False

    @abstractmethod
    def execute(self) -> ActionResult:
        """Execute the action and return a structured ActionResult."""
        pass
