"""Data models for the Assistant Orchestrator."""

from dataclasses import dataclass
from typing import Any

from avi.actions.base import BaseAction
from avi.execution.models import CommandRequest, ExecutionResult
from avi.providers.models import ResponseMetrics
from avi.safety.models import SafetyAssessment
from avi.tools.base import ToolResult


@dataclass
class OrchestratorResult:
    """Consolidated outcome returned by the Assistant Orchestrator."""

    text: str
    action: BaseAction | None = None
    tool_result: ToolResult | None = None
    command_request: CommandRequest | None = None
    execution_result: ExecutionResult | None = None
    safety_assessment: SafetyAssessment | None = None
    requires_confirmation: bool = False
    is_blocked: bool = False
    metrics: ResponseMetrics | None = None
    context: Any | None = None
