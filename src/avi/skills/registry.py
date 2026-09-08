"""Skill registry for discovering, validating, and executing modular agent skills."""

import logging
from typing import Any

from avi.apps.resolver import ApplicationResolver
from avi.skills.base import BaseSkill
from avi.skills.builtin import (
    ApplicationSkill,
    BrowserSkill,
    FilesystemSkill,
    MediaSkill,
    ScreenshotSkill,
    SystemControlsSkill,
    TerminalSkill,
)
from avi.skills.models import SkillAction, SkillResult

logger = logging.getLogger("avi.skills")


class SkillRegistry:
    """Central registry of modular skills available to the AVI agent."""

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """Register a new skill instance."""
        self._skills[skill.name] = skill
        logger.debug("Registered skill: %s", skill.name)

    def get(self, name: str) -> BaseSkill | None:
        """Retrieve a skill by name."""
        return self._skills.get(name)

    def list_skills(self) -> list[BaseSkill]:
        """Return all registered skills."""
        return list(self._skills.values())

    def find_skill_for_action(self, action: str) -> tuple[BaseSkill, SkillAction] | None:
        """Search across all skills for one that provides the given action."""
        for skill in self._skills.values():
            actions = skill.get_actions()
            if action in actions:
                return (skill, actions[action])
        return None

    def execute(
        self,
        skill_name: str,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Execute an action on a named skill, validating input and executing."""
        skill = self.get(skill_name)
        if skill is None:
            return SkillResult(
                success=False,
                action=action,
                error=f"Skill '{skill_name}' is not registered.",
            )

        actions = skill.get_actions()
        if action not in actions:
            return SkillResult(
                success=False,
                action=action,
                error=f"Skill '{skill_name}' does not provide action '{action}'.",
            )

        return skill.execute(action, parameters, context=context)


def create_default_skill_registry(
    app_resolver: ApplicationResolver | None = None,
) -> SkillRegistry:
    """Create and populate standard SkillRegistry with built-in skills."""
    resolver = app_resolver or ApplicationResolver()
    reg = SkillRegistry()
    reg.register(BrowserSkill(app_resolver=resolver))
    reg.register(ApplicationSkill(app_resolver=resolver))
    reg.register(FilesystemSkill())
    reg.register(SystemControlsSkill())
    reg.register(ScreenshotSkill())
    reg.register(TerminalSkill())
    reg.register(MediaSkill())
    return reg
