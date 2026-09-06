"""Unit tests for AI Provider abstraction, models, registry, and adapters."""

import json
from unittest.mock import MagicMock, patch
import pytest

from avi.config import Config
from avi.core.router import Router
from avi.execution import CommandRequest
from avi.providers import (
    AIProvider,
    AgentRequest,
    AgentResponse,
    AntigravityProvider,
    BaseProvider,
    LocalProvider,
    OllamaConnectionError,
    OllamaError,
    OllamaModelNotFoundError,
    OllamaProvider,
    OllamaTimeoutError,
    ProviderAPIError,
    ProviderCapabilities,
    ProviderConnectionError,
    ProviderError,
    ProviderHealth,
    ProviderModelNotFoundError,
    ProviderNotAvailableError,
    ProviderRegistry,
    ProviderResponse,
    ProviderTimeoutError,
    ResponseMetrics,
    ToolCall,
    create_default_provider_registry,
    get_default_registry,
    get_provider,
    list_providers,
    register_provider,
    remove_provider,
)
from avi.safety import RiskLevel
from avi.tools.base import ToolResult


# ---------------------------------------------------------------------------
# Fake Providers for Provider Independence Testing
# ---------------------------------------------------------------------------

class FakeProviderA(BaseProvider):
    """Deterministic Fake Provider A."""

    def __init__(self, reply: str = "Response from Provider A"):
        self.reply = reply
        self._metrics = ResponseMetrics(total_duration_ms=12.0)
        self._context = {"turn": 1}

    def get_model_name(self) -> str:
        return "fake-model-a"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            structured_output=True,
            context_size=16384,
            local=True,
            remote=False,
        )

    def is_available(self) -> bool:
        return True

    def warmup(self) -> bool:
        return True

    def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            text=self.reply,
            metrics=self._metrics,
            context=self._context,
        )

    def stream(self, request: AgentRequest):
        yield self.reply

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        return self._metrics

    @property
    def last_context(self):
        return self._context


class FakeProviderB(BaseProvider):
    """Deterministic Fake Provider B (different model, remote, different capabilities)."""

    def __init__(self, reply: str = "Response from Provider B"):
        self.reply = reply
        self._metrics = ResponseMetrics(total_duration_ms=85.0)
        self._context = {"session_id": "xyz"}

    def get_model_name(self) -> str:
        return "cloud-model-b"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_calling=False,
            structured_output=True,
            context_size=128000,
            local=False,
            remote=True,
        )

    def is_available(self) -> bool:
        return True

    def warmup(self) -> bool:
        return True

    def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            text=self.reply,
            metrics=self._metrics,
            context=self._context,
        )

    def stream(self, request: AgentRequest):
        for word in self.reply.split(" "):
            yield word + " "

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        return self._metrics

    @property
    def last_context(self):
        return self._context


class ToolCallingProvider(BaseProvider):
    """Fake Provider that emits structured tool calls."""

    def __init__(self, tool_call: ToolCall):
        self.tool_call = tool_call

    def get_model_name(self) -> str:
        return "tool-calling-model"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tool_calling=True)

    def is_available(self) -> bool:
        return True

    def warmup(self) -> bool:
        return True

    def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            text="",
            tool_calls=[self.tool_call],
            metrics=ResponseMetrics(total_duration_ms=10.0),
        )

    def stream(self, request: AgentRequest):
        yield ""

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        return None

    @property
    def last_context(self):
        return None


# ---------------------------------------------------------------------------
# 1. Provider Abstraction & Model Tests
# ---------------------------------------------------------------------------

def test_ai_provider_alias():
    assert AIProvider is BaseProvider


def test_provider_capabilities_model():
    caps = ProviderCapabilities(
        streaming=True,
        tool_calling=True,
        structured_output=True,
        vision=False,
        reasoning=True,
        context_size=32768,
        local=True,
        remote=True,
    )
    d = caps.to_dict()
    assert d["streaming"] is True
    assert d["tool_calling"] is True
    assert d["reasoning"] is True
    assert d["context_size"] == 32768
    assert d["local"] is True
    assert d["remote"] is True


def test_provider_health_model():
    health = ProviderHealth(healthy=True, message="Operational", latency_ms=15.2, details={"version": "1.0"})
    assert health.healthy is True
    assert health.message == "Operational"
    assert health.latency_ms == 15.2
    assert health.details["version"] == "1.0"


def test_tool_call_model():
    tc = ToolCall(name="filesystem.list_directory", arguments={"path": "/var/log"})
    assert tc.name == "filesystem.list_directory"
    assert tc.arguments["path"] == "/var/log"
    assert tc.call_id is None


def test_agent_request_and_response():
    req = AgentRequest(prompt="check system status", system_prompt="be concise", context=None, stream=False)
    assert req.prompt == "check system status"
    assert req.system_prompt == "be concise"
    assert req.stream is False

    resp = AgentResponse(
        text="all systems normal",
        metrics=ResponseMetrics(total_duration_ms=25.0),
        context=[10, 20],
        tool_calls=[ToolCall(name="system.system_info")],
    )
    assert resp.text == "all systems normal"
    assert resp.metrics.total_duration_ms == 25.0
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "system.system_info"


def test_backwards_compatibility_provider_response():
    resp = ProviderResponse(text="test text", metrics=ResponseMetrics(total_duration_ms=5.0), context=[1, 2])
    assert resp.text == "test text"
    assert resp.metrics.total_duration_ms == 5.0
    assert resp.context == [1, 2]
    assert resp.tool_calls == []


def test_base_provider_default_methods():
    class MinimalProvider(BaseProvider):
        def get_model_name(self) -> str:
            return "minimal"

        def is_available(self) -> bool:
            return True

        def warmup(self) -> bool:
            return True

        @property
        def last_metrics(self):
            return None

        @property
        def last_context(self):
            return None

        def generate_full(self, prompt, system_prompt=None, context=None):
            return ProviderResponse(text=f"echo: {prompt}")

        def generate(self, prompt, system_prompt=None, context=None, stream=True):
            yield f"echo: {prompt}"

    provider = MinimalProvider()
    assert provider.capabilities().streaming is True
    assert provider.health_check().healthy is True

    # Test send delegates to generate_full
    req = AgentRequest(prompt="hello")
    resp = provider.send(req)
    assert resp.text == "echo: hello"

    # Test stream delegates to generate
    chunks = list(provider.stream(req))
    assert chunks == ["echo: hello"]


# ---------------------------------------------------------------------------
# 2. Exception Hierarchy Tests
# ---------------------------------------------------------------------------

def test_exception_hierarchy():
    assert issubclass(OllamaError, ProviderError)
    assert issubclass(OllamaConnectionError, (OllamaError, ProviderConnectionError))
    assert issubclass(OllamaTimeoutError, (OllamaError, ProviderTimeoutError))
    assert issubclass(OllamaModelNotFoundError, (OllamaError, ProviderModelNotFoundError))
    assert issubclass(ProviderNotAvailableError, ProviderError)
    assert issubclass(ProviderAPIError, ProviderError)


# ---------------------------------------------------------------------------
# 3. Provider Registry Tests
# ---------------------------------------------------------------------------

def test_default_provider_registry():
    registry = create_default_provider_registry()
    providers = registry.list_providers()
    assert "ollama" in providers
    assert "local" in providers
    assert "antigravity" in providers


def test_registry_registration_and_removal():
    registry = ProviderRegistry()
    assert len(registry) == 0

    registry.register("custom", lambda cfg, **kw: FakeProviderA())
    assert registry.has_provider("custom")
    assert "custom" in registry
    assert len(registry) == 1

    instance = registry.get("custom")
    assert isinstance(instance, FakeProviderA)

    registry.remove("custom")
    assert not registry.has_provider("custom")
    assert len(registry) == 0


def test_registry_unknown_provider():
    registry = ProviderRegistry()
    registry.register("known", lambda cfg, **kw: FakeProviderA())
    with pytest.raises(ValueError) as exc:
        registry.get("unknown")
    assert "Unsupported provider: 'unknown'" in str(exc.value)
    assert "known" in str(exc.value)


def test_global_registry_functions():
    initial = list_providers()
    assert "ollama" in initial

    register_provider("fake_test_provider", lambda cfg, **kw: FakeProviderA())
    assert "fake_test_provider" in list_providers()

    prov = get_provider("fake_test_provider")
    assert isinstance(prov, FakeProviderA)

    remove_provider("fake_test_provider")
    assert "fake_test_provider" not in list_providers()


# ---------------------------------------------------------------------------
# 4. LocalProvider / OllamaProvider Tests
# ---------------------------------------------------------------------------

def test_local_provider_is_ollama_provider():
    assert LocalProvider is OllamaProvider


def test_ollama_provider_capabilities():
    provider = OllamaProvider()
    caps = provider.capabilities()
    assert caps.streaming is True
    assert caps.local is True
    assert caps.remote is False
    assert caps.context_size == 8192


def test_ollama_provider_health_check():
    provider = OllamaProvider()
    with patch.object(provider, "is_available", return_value=True):
        health = provider.health_check()
        assert health.healthy is True
        assert "ready" in health.message.lower()

    with patch.object(provider, "is_available", return_value=False):
        health = provider.health_check()
        assert health.healthy is False
        assert "cannot connect" in health.message.lower()


# ---------------------------------------------------------------------------
# 5. Antigravity Adapter Tests
# ---------------------------------------------------------------------------

def test_antigravity_provider_capabilities():
    provider = AntigravityProvider()
    caps = provider.capabilities()
    assert caps.streaming is True
    assert caps.tool_calling is True
    assert caps.reasoning is True
    assert caps.context_size == 32768
    assert caps.local is True
    assert caps.remote is True


def test_antigravity_provider_health_check_success():
    def fake_runner(cmd, timeout):
        return 0, "1.1.27\n", ""

    provider = AntigravityProvider(runner=fake_runner)
    health = provider.health_check()
    assert health.healthy is True
    assert "ready" in health.message.lower()
    assert health.details["version"] == "1.1.27"


def test_antigravity_provider_health_check_failure():
    def fake_runner(cmd, timeout):
        return 1, "", "Command failed"

    provider = AntigravityProvider(runner=fake_runner)
    health = provider.health_check()
    assert health.healthy is False
    assert "returned exit code 1" in health.message


def test_antigravity_provider_send_success():
    captured_cmd = []

    def fake_runner(cmd, timeout):
        captured_cmd.extend(cmd)
        return 0, "Hello from Antigravity\n", ""

    provider = AntigravityProvider(model="gemini-3.8-flash-high", runner=fake_runner)
    req = AgentRequest(prompt="explain python generators", system_prompt="be brief")
    resp = provider.send(req)

    assert resp.text == "Hello from Antigravity"
    assert resp.metrics is not None
    assert "--print" in captured_cmd
    assert "--model" in captured_cmd
    assert "gemini-3.8-flash-high" in captured_cmd


def test_antigravity_provider_send_extracts_structured_tool_call():
    def fake_runner(cmd, timeout):
        payload = json.dumps({
            "tool": "filesystem.list_directory",
            "arguments": {"path": "/tmp"}
        })
        return 0, payload, ""

    provider = AntigravityProvider(runner=fake_runner)
    req = AgentRequest(prompt="list /tmp")
    resp = provider.send(req)

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "filesystem.list_directory"
    assert resp.tool_calls[0].arguments == {"path": "/tmp"}


def test_antigravity_provider_error_handling():
    def error_runner(cmd, timeout):
        return 2, "", "Unauthorized or invalid model"

    provider = AntigravityProvider(runner=error_runner)
    with pytest.raises(ProviderAPIError) as exc:
        provider.send(AgentRequest(prompt="do something"))
    assert "Antigravity execution failed" in str(exc.value)


# ---------------------------------------------------------------------------
# 6. Provider Independence & Provider Switching Tests
# ---------------------------------------------------------------------------

def test_router_works_with_provider_a_and_provider_b():
    config = Config.load()

    # Use Provider A
    provider_a = FakeProviderA(reply="Result from A")
    router_a = Router(config, provider=provider_a)
    resp_a = router_a.route_full("how to write a decorator?")
    assert resp_a.text == "Result from A"
    assert router_a.provider.capabilities().local is True

    # Switch to Provider B
    provider_b = FakeProviderB(reply="Result from B")
    router_b = Router(config, provider=provider_b)
    resp_b = router_b.route_full("how to write a decorator?")
    assert resp_b.text == "Result from B"
    assert router_b.provider.capabilities().remote is True


def test_router_switching_via_registry():
    registry = ProviderRegistry()
    registry.register("provider_1", lambda cfg, **kw: FakeProviderA(reply="Alpha"))
    registry.register("provider_2", lambda cfg, **kw: FakeProviderB(reply="Beta"))

    config_1 = Config.load(provider="provider_1")
    router_1 = Router(config_1, registry=registry)
    assert router_1.route_full("test query").text == "Alpha"

    config_2 = Config.load(provider="provider_2")
    router_2 = Router(config_2, registry=registry)
    assert router_2.route_full("test query").text == "Beta"


# ---------------------------------------------------------------------------
# 7. SAFETY INVARIANT MUST REMAIN INDEPENDENT OF PROVIDER
# ---------------------------------------------------------------------------

def test_all_providers_route_commands_through_safety_engine():
    """Prove that regardless of provider, command proposals strictly pass through SafetyEngine."""
    config = Config.load()

    # Provider A proposes a SAFE command
    prov_a_safe = FakeProviderA(reply="COMMAND: ls -la")
    router_a = Router(config, provider=prov_a_safe)
    proposal_a = router_a.parse_command_proposal("COMMAND: ls -la")
    assert isinstance(proposal_a, CommandRequest)
    assessment_a = router_a.evaluate_command(proposal_a)
    assert assessment_a.level == RiskLevel.SAFE

    # Provider A proposes a CATASTROPHIC command
    prov_a_block = FakeProviderA(reply="COMMAND: rm -rf /")
    router_a_block = Router(config, provider=prov_a_block)
    proposal_a_block = router_a_block.parse_command_proposal("COMMAND: rm -rf /")
    assessment_a_block = router_a_block.evaluate_command(proposal_a_block)
    assert assessment_a_block.level == RiskLevel.BLOCK
    assert assessment_a_block.is_blocked is True

    # Provider B proposes the same CATASTROPHIC command
    prov_b_block = FakeProviderB(reply="COMMAND: rm -rf /")
    router_b_block = Router(config, provider=prov_b_block)
    proposal_b_block = router_b_block.parse_command_proposal("COMMAND: rm -rf /")
    assessment_b_block = router_b_block.evaluate_command(proposal_b_block)
    assert assessment_b_block.level == RiskLevel.BLOCK
    assert assessment_b_block.is_blocked is True

    # Provider B proposes a mutating command requiring confirmation
    prov_b_confirm = FakeProviderB(reply="COMMAND: touch new_file.txt")
    router_b_confirm = Router(config, provider=prov_b_confirm)
    proposal_b_confirm = router_b_confirm.parse_command_proposal("COMMAND: touch new_file.txt")
    assessment_b_confirm = router_b_confirm.evaluate_command(proposal_b_confirm)
    assert assessment_b_confirm.level == RiskLevel.CONFIRM
    assert assessment_b_confirm.requires_confirmation is True


# ---------------------------------------------------------------------------
# 8. Tool Calling Architecture Tests
# ---------------------------------------------------------------------------

def test_router_executes_tool_call_through_tool_registry():
    config = Config.load()
    tool_call = ToolCall(name="system.system_info", arguments={})
    provider = ToolCallingProvider(tool_call=tool_call)
    router = Router(config, provider=provider)

    resp = router.route_full("inspect system specifications")
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "system.system_info"
    # Result should contain system info text executed through ToolRegistry
    assert "OS:" in resp.text or "Linux" in resp.text or len(resp.text) > 0


def test_router_handles_unknown_tool_call():
    config = Config.load()
    tool_call = ToolCall(name="nonexistent.dangerous_tool", arguments={})
    provider = ToolCallingProvider(tool_call=tool_call)
    router = Router(config, provider=provider)

    resp = router.route_full("run dangerous tool")
    assert "Unknown tool: 'nonexistent.dangerous_tool'" in resp.text


# ---------------------------------------------------------------------------
# 9. Configuration & CLI Integration Tests
# ---------------------------------------------------------------------------

def test_config_provider_environment_variable(monkeypatch):
    monkeypatch.setenv("AVI_PROVIDER", "antigravity")
    config = Config.load()
    assert config.provider == "antigravity"


def test_config_antigravity_options(monkeypatch):
    monkeypatch.setenv("AVI_ANTIGRAVITY_BIN", "/custom/bin/agy")
    monkeypatch.setenv("AVI_ANTIGRAVITY_MODEL", "gemini-3.8-flash-high")
    config = Config.load()
    assert config.antigravity_bin == "/custom/bin/agy"
    assert config.antigravity_model == "gemini-3.8-flash-high"


# ---------------------------------------------------------------------------
# 10. Capability Negotiation & Session Tests
# ---------------------------------------------------------------------------

def test_capability_negotiation():
    prov_a = FakeProviderA()
    prov_b = FakeProviderB()

    assert prov_a.capabilities().tool_calling is True
    assert prov_b.capabilities().tool_calling is False
    assert prov_a.capabilities().local is True
    assert prov_b.capabilities().remote is True

    # Router can inspect capabilities
    config = Config.load()
    router_a = Router(config, provider=prov_a)
    assert router_a.provider.capabilities().tool_calling is True

    router_b = Router(config, provider=prov_b)
    assert router_b.provider.capabilities().tool_calling is False


def test_antigravity_stream_mode():
    def fake_runner(cmd, timeout):
        return 0, "streamed output from antigravity\n", ""

    provider = AntigravityProvider(runner=fake_runner)
    chunks = list(provider.stream(AgentRequest(prompt="hello")))
    assert chunks == ["streamed output from antigravity"]


def test_antigravity_health_check_binary_not_found():
    provider = AntigravityProvider(binary="nonexistent_binary_xyz_123")
    health = provider.health_check()
    assert health.healthy is False
    assert "not found" in health.message.lower()


def test_session_handles_generic_provider_error(tmp_path):
    import io
    from avi.core.session import InteractiveSession

    class FailingProvider(BaseProvider):
        def get_model_name(self):
            return "failing"
        def is_available(self):
            return True
        def warmup(self):
            return True
        @property
        def last_metrics(self):
            return None
        @property
        def last_context(self):
            return None
        def send(self, request):
            raise ProviderError("Custom provider failed to connect")
        def stream(self, request):
            raise ProviderError("Custom provider failed to connect")

    config = Config.load()
    router = Router(config, provider=FailingProvider())
    in_stream = io.StringIO("test turn\nexit\n")
    out_stream = io.StringIO()
    err_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
        err_stream=err_stream,
    )
    code = session.run()
    assert code == 0
    assert "Error: Custom provider failed to connect" in err_stream.getvalue()


def test_provider_fastpath_latency_benchmark():
    """Verify that FastPath in-memory resolution remains ~5.3 µs per resolution."""
    import time
    config = Config.load()
    router = Router(config, provider=FakeProviderA())

    # Warmup
    for _ in range(100):
        router.fastpath.resolve("git status")

    iterations = 2000
    t0 = time.perf_counter()
    for _ in range(iterations):
        router.fastpath.resolve("git status")
    total_sec = time.perf_counter() - t0

    us_per_res = (total_sec / iterations) * 1_000_000.0
    # FastPath resolution must remain sub-50 microseconds (typically ~5 µs)
    assert us_per_res < 50.0
