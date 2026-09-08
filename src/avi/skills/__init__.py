"""Modular skill architecture for AVI."""

from avi.skills.base import BaseSkill
from avi.skills.models import SkillAction, SkillResult
from avi.skills.registry import SkillRegistry, create_default_skill_registry

__all__ = [
    "BaseSkill",
    "SkillAction",
    "SkillResult",
    "SkillRegistry",
    "create_default_skill_registry",
]
