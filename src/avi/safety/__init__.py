"""Safety and command risk assessment subsystem for AVI (Phase 5).

Provides deterministic command classification:
- SAFE: execute automatically
- CONFIRM: request explicit user confirmation
- BLOCK: refuse catastrophic or malformed commands
"""

from avi.safety.engine import SafetyEngine
from avi.safety.models import RiskLevel, SafetyAssessment
from avi.safety.parser import ParsedCommand, parse_command_safety

__all__ = [
    "SafetyEngine",
    "RiskLevel",
    "SafetyAssessment",
    "ParsedCommand",
    "parse_command_safety",
]
