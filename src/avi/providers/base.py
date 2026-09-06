"""Base AI provider abstraction for AVI."""

from abc import ABC, abstractmethod
from typing import Any, Iterator

from avi.providers.models import (
    AgentRequest,
    AgentResponse,
    ProviderCapabilities,
    ProviderHealth,
    ProviderResponse,
    ResponseMetrics,
    ToolCall,
)


class BaseProvider(ABC):
    """Abstract base class for all AI model backends.

    AVI Core is provider-independent. All model integrations (Local/Ollama,
    Antigravity, OpenAI, Anthropic, Gemini, etc.) implement this interface
    or inherit from it.
    """

    def send(self, request: AgentRequest) -> AgentResponse:
        """Send a normalized request and return a structured response."""
        return self.generate_full(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            context=request.context,
        )

    def stream(self, request: AgentRequest) -> Iterator[str]:
        """Send a normalized request and stream text response chunks."""
        yield from self.generate(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            context=request.context,
            stream=request.stream,
        )

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: Any | None = None,
        stream: bool = True,
    ) -> Iterator[str]:
        """Legacy generation method. Streams or yields text chunks."""
        req = AgentRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
            stream=stream,
        )
        yield from self.stream(req)

    def generate_full(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: Any | None = None,
    ) -> ProviderResponse:
        """Legacy synchronous generation method. Returns complete response."""
        req = AgentRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
            stream=False,
        )
        return self.send(req)

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities supported by this provider."""
        return ProviderCapabilities()

    def health_check(self) -> ProviderHealth:
        """Perform a health and reachability check on the provider backend."""
        healthy = self.is_available()
        return ProviderHealth(
            healthy=healthy,
            message="Ready" if healthy else "Provider unavailable",
        )

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the backend is reachable and ready."""
        pass

    @abstractmethod
    def warmup(self) -> bool:
        """Warm up the model backend into memory."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the active model name."""
        pass

    @property
    @abstractmethod
    def last_metrics(self) -> ResponseMetrics | None:
        """Return the metrics from the most recent generation, if available."""
        pass

    @property
    @abstractmethod
    def last_context(self) -> Any | None:
        """Return conversation context token state from the most recent generation."""
        pass


class LLMProvider(BaseProvider):
    """Stable assistant-facing interface for language models.

    Separation of concerns:
    - Provider supplies reasoning, text generation, streaming, and tool selection proposals.
    - AVI owns tools, execution, permissions, safety policy, and system actions.
    """

    def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        """Multi-turn chat completion with optional tool definitions."""
        prompt_lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages]
        return self.send(
            AgentRequest(
                prompt="\n".join(prompt_lines),
                system_prompt=system_prompt,
            )
        )

    def select_tool(
        self,
        prompt: str,
        available_tools: list[dict[str, Any]],
        system_prompt: str | None = None,
    ) -> ToolCall | None:
        """Use model intelligence to select a tool call from available schemas."""
        resp = self.send(
            AgentRequest(
                prompt=prompt,
                system_prompt=system_prompt,
            )
        )
        if resp.tool_calls:
            return resp.tool_calls[0]
        return None


# Conceptual aliases for providers
AIProvider = BaseProvider

__all__ = [
    "AIProvider",
    "BaseProvider",
    "LLMProvider",
    "AgentRequest",
    "AgentResponse",
    "ProviderResponse",
    "ResponseMetrics",
    "ProviderCapabilities",
    "ProviderHealth",
    "ToolCall",
]
