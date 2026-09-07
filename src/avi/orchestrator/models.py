import time
from dataclasses import dataclass, field
from typing import Any

from avi.actions.base import BaseAction
from avi.execution.models import CommandRequest, ExecutionResult
from avi.providers.models import ResponseMetrics
from avi.safety.models import SafetyAssessment
from avi.tools.base import ToolResult


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
