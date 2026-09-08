"""Provider-independent models, capabilities, and exception hierarchy for AVI."""

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Provider Exception Hierarchy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base exception for all AI model provider errors."""

    pass


class ProviderConnectionError(ProviderError):
    """Raised when provider backend is unreachable or connection is refused."""

    pass


class ProviderTimeoutError(ProviderError):
    """Raised when a request to a provider times out."""

    pass


class ProviderModelNotFoundError(ProviderError):
    """Raised when the requested model is not found or not supported."""

    pass


class ProviderAuthError(ProviderError):
    """Raised on authentication or authorization failure."""

    pass


class ProviderAPIError(ProviderError):
    """Raised when a provider returns an unexpected API error."""

    pass


class ProviderNotAvailableError(ProviderError, ValueError):
    """Raised when a requested provider is not installed or available."""

    pass


# ---------------------------------------------------------------------------
# Provider Capabilities & Health
# ---------------------------------------------------------------------------


@dataclass
class ProviderCapabilities:
    """Explicit capabilities supported by an AI provider."""

    streaming: bool = True
    tool_calling: bool = False
    structured_output: bool = False
    vision: bool = False
    reasoning: bool = False
    text_reasoning: bool = True
    tool_reasoning: bool = False
    context_size: int = 8192
    local: bool = True
    remote: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert capabilities to a dictionary representation."""
        return {
            "streaming": self.streaming,
            "tool_calling": self.tool_calling,
            "structured_output": self.structured_output,
            "vision": self.vision,
            "reasoning": self.reasoning,
            "text_reasoning": self.text_reasoning,
            "tool_reasoning": self.tool_reasoning,
            "context_size": self.context_size,
            "local": self.local,
            "remote": self.remote,
        }

    def supports(self, required: "ProviderCapabilities") -> bool:
        """Check if these capabilities satisfy all required capability flags."""
        if required.streaming and not self.streaming:
            return False
        if required.tool_calling and not self.tool_calling:
            return False
        if required.structured_output and not self.structured_output:
            return False
        if required.vision and not self.vision:
            return False
        if required.reasoning and not self.reasoning:
            return False
        if required.text_reasoning and not self.text_reasoning:
            return False
        if required.tool_reasoning and not self.tool_reasoning:
            return False
        return True


@dataclass
class ProviderHealth:
    """Health and reachability status for an AI provider."""

    healthy: bool
    message: str = "OK"
    latency_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalized Tool Calls & Requests / Responses
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """Structured tool invocation requested by an AI model."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass
class ResponseMetrics:
    """Performance and latency metrics for an inference request."""

    total_duration_ms: float
    load_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    eval_duration_ms: float | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    intent_duration_ms: float | None = None
    capability_duration_ms: float | None = None
    retrieval_duration_ms: float | None = None
    time_to_first_token_ms: float | None = None
    routing_duration_ms: float | None = None
    llm_duration_ms: float | None = None


@dataclass
class AgentRequest:
    """Normalized request dispatched to an AI provider."""

    prompt: str
    system_prompt: str | None = None
    context: Any | None = None
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    stream: bool = True


@dataclass
class AgentResponse:
    """Normalized response received from an AI provider."""

    text: str
    metrics: ResponseMetrics | None = None
    context: Any | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    raw: Any | None = None


# Backwards compatibility alias
ProviderResponse = AgentResponse
