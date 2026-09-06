"""Data models for safety assessment, action categories, and risk levels."""

from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    """Risk classification level for proposed commands and actions."""

    SAFE = "SAFE"
    CONFIRM = "CONFIRM"
    BLOCK = "BLOCK"


class ActionCategory(str, Enum):
    """Semantic category describing the nature and scope of an action."""

    READ_ONLY = "READ_ONLY"
    LOW_RISK_ACTION = "LOW_RISK_ACTION"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"
    FILESYSTEM_WRITE = "FILESYSTEM_WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    PRIVILEGED = "PRIVILEGED"


@dataclass(frozen=True)
class SafetyAssessment:
    """Structured decision from the safety evaluation engine."""

    level: RiskLevel
    reason: str
    command: str = ""
    category: ActionCategory = ActionCategory.FILESYSTEM_WRITE

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

    def format_confirmation_prompt(self) -> str:
        """Return a risk-appropriate, human-friendly confirmation message."""
        cmd_str = self.command or "(unspecified)"

        if self.category == ActionCategory.LOW_RISK_ACTION:
            warning = "This action will launch an application or external process."
        elif self.category == ActionCategory.EXTERNAL_ACTION:
            warning = "This action will open an external link or service."
        elif self.category == ActionCategory.DESTRUCTIVE:
            warning = "WARNING: This command performs destructive deletion or modification."
        elif self.category == ActionCategory.PRIVILEGED:
            warning = "CRITICAL: This command requires elevated system privileges."
        elif self.category == ActionCategory.READ_ONLY:
            warning = "This is a read-only inspection."
        else:
            # Filesystem write or general system state change
            warning = "This command can modify system or filesystem state."

        reason_line = f"Reason: {self.reason}\n" if self.reason else ""
        return f"\nCommand:\n{cmd_str}\n\n{reason_line}{warning}\n\nExecute? [y/N] "
