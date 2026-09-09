import time
from dataclasses import dataclass, field
from typing import Any

from avi.actions.base import BaseAction
from avi.execution.models import CommandRequest, ExecutionResult
from avi.providers.models import ResponseMetrics
from avi.safety.models import SafetyAssessment
from avi.tools.base import ToolResult


@dataclass
class PendingClarification:
    """State of an ambiguous or shorthand request awaiting user confirmation."""

    original_prompt: str
    proposed_interpretation: str
    clarification_type: str = "typo"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, ttl_seconds: float = 180.0) -> bool:
        """Check if pending clarification has expired."""
        return (time.time() - self.created_at) > ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_prompt": self.original_prompt,
            "proposed_interpretation": self.proposed_interpretation,
            "clarification_type": self.clarification_type,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingClarification | None":
        if not isinstance(data, dict):
            return None
        orig = data.get("original_prompt")
        prop = data.get("proposed_interpretation")
        if not orig or not prop:
            return None
        return cls(
            original_prompt=str(orig),
            proposed_interpretation=str(prop),
            clarification_type=str(data.get("clarification_type", "typo")),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ConversationTurn:
    """A single turn in an assistant conversation."""

    turn_id: int
    user_query: str
    intent_type: str
    response_text: str
    tool_result: ToolResult | None = None
    action: BaseAction | None = None
    command_request: CommandRequest | None = None
    execution_result: ExecutionResult | None = None
    plan: Any | None = None
    capability_result: Any | None = None
    target: str | None = None
    search_results: list[Any] | None = None
    selected_result: Any | None = None
    pending_clarification: PendingClarification | None = None
    timestamp: float = field(default_factory=time.time)


class ConversationHistory:
    """Maintains a bounded sequence of conversation turns for multi-turn assistant context."""

    def __init__(self, max_turns: int = 50, persist_state: bool = True) -> None:
        self.max_turns = max_turns
        self.persist_state = persist_state
        self.turns: list[ConversationTurn] = []

    def add_turn(
        self,
        user_query: str,
        intent_type: str,
        response_text: str,
        tool_result: ToolResult | None = None,
        action: BaseAction | None = None,
        command_request: CommandRequest | None = None,
        execution_result: ExecutionResult | None = None,
        plan: Any | None = None,
        capability_result: Any | None = None,
        target: str | None = None,
        search_results: list[Any] | None = None,
        selected_result: Any | None = None,
        pending_clarification: PendingClarification | None = None,
    ) -> ConversationTurn:
        turn = ConversationTurn(
            turn_id=len(self.turns) + 1,
            user_query=user_query,
            intent_type=intent_type,
            response_text=response_text,
            target=target,
            tool_result=tool_result,
            action=action,
            command_request=command_request,
            execution_result=execution_result,
            plan=plan,
            capability_result=capability_result,
            search_results=search_results,
            selected_result=selected_result,
            pending_clarification=pending_clarification,
            timestamp=time.time(),
        )
        self.turns.append(turn)
        if len(self.turns) > self.max_turns:
            self.turns.pop(0)

        if self.persist_state:
            try:
                from avi.session.state import save_session_state

                save_session_state(turn)
            except Exception:
                pass

        return turn

    @property
    def last_turn(self) -> ConversationTurn | None:
        if self.turns:
            return self.turns[-1]
        if self.persist_state:
            try:
                from avi.session.state import load_session_state

                return load_session_state()
            except Exception:
                return None
        return None

    def clear(self) -> None:
        self.turns.clear()
        if self.persist_state:
            try:
                from avi.session.state import clear_session_state

                clear_session_state()
            except Exception:
                pass

    def __len__(self) -> int:
        return len(self.turns)


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
    plan: Any | None = None
    capability_result: Any | None = None
    search_results: list[Any] | None = None
    selected_result: Any | None = None
    action_url: str | None = None
    pending_clarification: PendingClarification | None = None
