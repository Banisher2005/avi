"""Unit tests for AssistantOrchestrator."""

from unittest.mock import MagicMock, patch

from avi.apps.models import ApplicationResolution
from avi.config import Config
from avi.core.router import Router
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.safety.models import RiskLevel
from avi.tools.base import ToolResult


class DummyProvider(BaseProvider):
    def __init__(self, reply: str = "Dummy response"):
        self.reply = reply
        self._metrics = ResponseMetrics(total_duration_ms=10.0)

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        yield self.reply

    def generate_full(self, prompt, system_prompt=None, context=None):
        return ProviderResponse(text=self.reply, metrics=self._metrics)

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "dummy-model"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return None


class TestAssistantOrchestrator:
    def setup_method(self):
        self.config = Config.load()
        self.provider = DummyProvider()
        self.router = Router(self.config, provider=self.provider)
        self.mock_resolver = MagicMock()
        self.orchestrator = AssistantOrchestrator(
            config=self.config,
            router=self.router,
            app_resolver=self.mock_resolver,
        )

    def test_greeting_handling(self):
        res = self.orchestrator.handle("hi")
        assert res.text == "Hello! How can I help?"
        assert res.action is None
        assert res.tool_result is None

    def test_capabilities_handling(self):
        res = self.orchestrator.handle("what can you do?")
        assert "I can help with your computer" in res.text

    def test_disk_space_uses_tool_and_synthesizes(self):
        mock_disk_tool = MagicMock()
        mock_disk_tool.name = "system.disk_usage"
        mock_disk_tool.execute.return_value = ToolResult(
            success=True,
            data={"free_bytes": 100 * (1024**3), "total_bytes": 200 * (1024**3), "path": "/"},
        )
        self.orchestrator.tools.register(mock_disk_tool)

        res = self.orchestrator.handle("how much space is left on my laptop")
        assert "100 GB free" in res.text
        assert "200 GB" in res.text
        mock_disk_tool.execute.assert_called_once_with(path="/")

    def test_memory_total_handling(self):
        with patch("avi.orchestrator.orchestrator.get_memory_summary_conversational") as mock_mem:
            mock_mem.return_value = "You have about 9.2 GB of RAM available out of 14.5 GB total."
            res = self.orchestrator.handle("how much memory is free")
            assert "9.2 GB of RAM available" in res.text
            mock_mem.assert_called_once()

    def test_timer_action_execution(self):
        with patch("time.sleep") as mock_sleep:
            res = self.orchestrator.handle("set a timer for 2 seconds", auto_execute_actions=True)
            assert "Timer set for 2 seconds." in res.text
            assert "Time's up." in res.text
            assert res.action is not None
            mock_sleep.assert_called_once_with(2.0)

    def test_open_app_installed(self):
        self.mock_resolver.resolve.return_value = ApplicationResolution(
            requested_name="brave",
            canonical_name="Brave Origin",
            executable="/usr/bin/brave-origin",
            desktop_entry=None,
            platform="Linux",
            installed=True,
            confidence=0.9,
        )
        self.mock_resolver.launch.return_value = (True, "Opening Brave Origin.")

        res = self.orchestrator.handle("open brave", auto_execute_actions=True)
        assert res.text == "Opening Brave Origin."
        assert res.action is not None

    def test_open_app_uninstalled_reports_cleanly(self):
        self.mock_resolver.resolve.return_value = ApplicationResolution(
            requested_name="nonexistent",
            canonical_name="Nonexistent",
            executable=None,
            desktop_entry=None,
            platform="Linux",
            installed=False,
            confidence=0.0,
        )

        res = self.orchestrator.handle("open nonexistent", auto_execute_actions=True)
        assert "Application 'Nonexistent' is not installed on this system." in res.text
        assert res.action is None

    def test_open_url_action(self):
        with patch("webbrowser.open", return_value=True):
            res = self.orchestrator.handle("open https://github.com", auto_execute_actions=True)
            assert "Opening https://github.com." in res.text
            assert res.action is not None

    def test_shell_command_confirm_classification(self):
        # Provider suggests a modifying command
        self.router._provider = DummyProvider("COMMAND: mkdir new_folder")
        res = self.orchestrator.handle("create a folder named new_folder")

        assert res.requires_confirmation is True
        assert res.command_request is not None
        assert res.command_request.command_line == "mkdir new_folder"
        assert "Execute? [y/N]" in res.text
        assert res.safety_assessment.level == RiskLevel.CONFIRM

    def test_shell_command_blocked_classification(self):
        # Catastrophic command
        self.router._provider = DummyProvider("COMMAND: rm -rf /")
        res = self.orchestrator.handle("delete everything on root")

        assert res.is_blocked is True
        assert "Blocked:" in res.text
        assert res.safety_assessment.level == RiskLevel.BLOCK
