"""Unit tests for the provider-agnostic LLMProvider abstraction and provider switching."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from avi.config import Config
from avi.core.router import Router
from avi.providers.base import (
    AIProvider,
    AgentRequest,
    AgentResponse,
    BaseProvider,
    LLMProvider,
    ProviderCapabilities,
    ProviderHealth,
    ProviderResponse,
    ResponseMetrics,
    ToolCall,
)
from avi.providers.registry import ProviderRegistry


class MockLLM(LLMProvider):
    def __init__(self, name: str = "mock-llm"):
        self._name = name
        self._metrics = ResponseMetrics(total_duration_ms=12.0)

    def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            text=f"Response from {self._name}",
            metrics=self._metrics,
        )

    def is_available(self) -> bool:
        return True

    def warmup(self) -> bool:
        return True

    def get_model_name(self) -> str:
        return self._name

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        return self._metrics

    @property
    def last_context(self) -> Any | None:
        return None


class TestLLMProviderInterface:
    def test_llm_provider_inherits_base_provider(self):
        assert issubclass(LLMProvider, BaseProvider)

    def test_chat_method(self):
        provider = MockLLM("chat-provider")
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you?"},
        ]
        resp = provider.chat(messages, system_prompt="You are helpful.")
        assert resp.text == "Response from chat-provider"

    def test_select_tool_method(self):
        provider = MockLLM("tool-provider")
        # Mock send to return a ToolCall
        expected_call = ToolCall(name="system.disk_usage", arguments={"path": "/"})
        provider.send = MagicMock(return_value=AgentResponse(text="", tool_calls=[expected_call]))

        tools = [{"name": "system.disk_usage", "description": "Check disk space"}]
        selected = provider.select_tool("how much space is left?", available_tools=tools)

        assert selected is not None
        assert selected.name == "system.disk_usage"
        assert selected.arguments == {"path": "/"}

    def test_provider_switching_in_registry(self):
        registry = ProviderRegistry()
        mock_a = MockLLM("provider-a")
        mock_b = MockLLM("provider-b")

        registry.register("provider-a", lambda config: mock_a)
        registry.register("provider-b", lambda config: mock_b)

        config_a = Config.load(provider="provider-a")
        config_b = Config.load(provider="provider-b")

        p1 = registry.get("provider-a", config=config_a)
        p2 = registry.get("provider-b", config=config_b)

        assert p1.get_model_name() == "provider-a"
        assert p2.get_model_name() == "provider-b"
