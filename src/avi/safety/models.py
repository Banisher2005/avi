"""Data models for safety assessment and risk levels."""

from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    """Risk classification level for proposed commands."""

    SAFE = "SAFE"
    CONFIRM = "CONFIRM"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class SafetyAssessment:
    """Structured decision from the safety evaluation engine."""

    level: RiskLevel
    reason: str
    command: str = ""

    @property
    def is_safe(self) -> bool:
        """True if command is safe to execute without user confirmation."""
        return self.level == RiskLevel.SAFE

    @property
    def requires_confirmation(self) -> bool:
        """True if command modifies state and requires explicit confirmation."""
        return self.level == RiskLevel.CONFIRM

    @property
    def is_blocked(self) -> bool:
        """True if command is catastrophic, malicious, or malformed."""
        return self.level == RiskLevel.BLOCK
