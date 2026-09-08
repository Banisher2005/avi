"""Base skill abstraction for AVI skill architecture."""

from abc import ABC, abstractmethod
from typing import Any

from avi.skills.models import SkillAction, SkillResult


class BaseSkill(ABC):
    """Abstract base class for modular agent skills."""

    name: str = "base_skill"
    description: str = "Base skill description"

    @abstractmethod
    def get_actions(self) -> dict[str, SkillAction]:
        """Return a mapping of action_name -> SkillAction metadata."""
        pass

    @abstractmethod
    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Execute a specific action provided by this skill."""
        pass

    def verify(
        self,
        action: str,
        parameters: dict[str, Any],
        result: SkillResult,
    ) -> bool:
        """Verify post-conditions for the executed action."""
        # By default, verify checks that the result succeeded and no error was reported
        return result.success and result.error is None
