"""Comprehensive Acceptance Test Suite for AVI Desktop Assistant Runtime.

Covers:
- Category A: Basic Conversation (Greetings, Capabilities, Small Talk, Courtesies)
- Category B: System Questions (Disk Space, Memory, RAM/CPU Processes, OS/Kernel Info)
- Category C: Native Desktop Actions (URLs, Files, Directory Aliases, Applications, Timers)
- Category D: Conversational Follow-up State (Turn History, Context Continuity)
- Category E: Clarification of Ambiguous / Deictic Requests
- Category F: Invalid Requests & Helpful Guidance
- Category G: Safety Invariants & Execution Boundaries
"""

from unittest.mock import MagicMock, patch

import pytest

from avi.actions.system import OpenAppAction, OpenDirAction, OpenUrlAction
from avi.actions.timer import TimerAction
from avi.apps.models import ApplicationResolution
from avi.config import Config
from avi.core.router import Router
from avi.orchestrator.models import ConversationHistory, OrchestratorResult
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.safety.models import RiskLevel
from avi.tools.base import ToolResult


class MockAssistantProvider(BaseProvider):
    """Deterministic mock provider for testing fallback reasoning."""

    def __init__(self, default_reply: str = "Assistant reasoning fallback"):
        self.default_reply = default_reply
        self._metrics = ResponseMetrics(total_duration_ms=12.0)
        self.last_prompt = None

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        self.last_prompt = prompt
        yield self.default_reply

    def generate_full(self, prompt, system_prompt=None, context=None):
        self.last_prompt = prompt
        return ProviderResponse(text=self.default_reply, metrics=self._metrics, context=[1, 2])

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "mock-assistant"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return [1, 2]


@pytest.fixture
def orchestrator():
    """Create an isolated AssistantOrchestrator instance for acceptance testing."""
    config = Config.load()
    provider = MockAssistantProvider()
    router = Router(config, provider=provider)
    mock_resolver = MagicMock()
    orch = AssistantOrchestrator(
        config=config,
        router=router,
        app_resolver=mock_resolver,
    )
    return orch


# ==============================================================================
# Category A: Basic Conversation
# ==============================================================================
class TestCategoryABasicConversation:
    @pytest.mark.parametrize(
        "query",
        ["hi", "hello", "Hey!", "Good morning", "howdy", "yo"],
    )
    def test_greetings_produce_natural_greeting(self, orchestrator, query):
        res = orchestrator.handle(query)
        assert isinstance(res, OrchestratorResult)
        assert "Hello!" in res.text
        assert res.action is None
        assert res.tool_result is None
        assert res.command_request is None

    @pytest.mark.parametrize(
        "query",
        [
            "what can you do?",
            "what are your capabilities",
            "help",
            "can you help me?",
            "what is avi?",
        ],
    )
    def test_capabilities_describe_assistant_features(self, orchestrator, query):
        res = orchestrator.handle(query)
        assert "I can help with your computer" in res.text
        assert "system information" in res.text

    @pytest.mark.parametrize(
        "query",
        ["what's up", "how are you doing?", "how's it going"],
    )
    def test_small_talk_responds_readily(self, orchestrator, query):
        res = orchestrator.handle(query)
        assert "I'm ready!" in res.text or "Hello! How can I help?" in res.text

    @pytest.mark.parametrize(
        "query",
        ["thanks", "thank you", "thank you very much!", "thx", "cheers", "appreciate it"],
    )
    def test_courtesy_acknowledges_gracefully(self, orchestrator, query):
        res = orchestrator.handle(query)
        assert "You're welcome!" in res.text
        assert res.action is None


# ==============================================================================
# Category B: System Questions (Native Read-Only Tools)
# ==============================================================================
class TestCategoryBSystemQuestions:
    def test_disk_space_synthesizes_natural_language(self, orchestrator):
        mock_tool = MagicMock()
        mock_tool.name = "system.disk_usage"
        free_bytes = 154 * (1024**3)
        total_bytes = 240 * (1024**3)
        mock_tool.execute.return_value = ToolResult(
            success=True,
            data={"free_bytes": free_bytes, "total_bytes": total_bytes, "path": "/"},
        )
        orchestrator.tools.register(mock_tool)

        res = orchestrator.handle("how much space is left on my laptop?")
        assert "154 GB free" in res.text
        assert "240 GB" in res.text
        assert "main drive" in res.text
        assert res.tool_result is not None
        assert res.command_request is None

    def test_memory_total_overview(self, orchestrator):
        with patch("avi.orchestrator.orchestrator.get_memory_summary_conversational") as mock_mem:
            mock_mem.return_value = (
                "You have about 9.1 GB of RAM available out of 14.5 GB total (37% in use)."
            )
            res = orchestrator.handle("how much memory is free?")
            assert "9.1 GB of RAM available" in res.text
            assert "37% in use" in res.text
            assert res.command_request is None

    def test_ram_usage_by_process(self, orchestrator):
        mock_tool = MagicMock()
        mock_tool.name = "system.processes"
        mock_tool.execute.return_value = ToolResult(
            success=True,
            data=[
                {"pid": 101, "name": "llama-server", "memory_percent": 8.2, "cpu_percent": 2.1},
                {"pid": 102, "name": "firefox", "memory_percent": 4.5, "cpu_percent": 1.0},
            ],
        )
        orchestrator.tools.register(mock_tool)

        res = orchestrator.handle("what is using the most ram?")
        assert "llama-server is currently using the most memory at about 8.2%" in res.text
        assert "firefox" in res.text

    def test_cpu_usage_by_process(self, orchestrator):
        mock_tool = MagicMock()
        mock_tool.name = "system.processes"
        mock_tool.execute.return_value = ToolResult(
            success=True,
            data=[
                {"pid": 201, "name": "ffmpeg", "memory_percent": 2.0, "cpu_percent": 85.4},
                {"pid": 202, "name": "code", "memory_percent": 3.0, "cpu_percent": 12.1},
            ],
        )
        orchestrator.tools.register(mock_tool)

        res = orchestrator.handle("what is using the most cpu?")
        assert "ffmpeg is currently using the most CPU at about 85.4%" in res.text

    def test_running_processes_summary(self, orchestrator):
        mock_tool = MagicMock()
        mock_tool.name = "system.processes"
        mock_tool.execute.return_value = ToolResult(
            success=True,
            data=[
                {"pid": 1, "name": "systemd", "memory_percent": 0.5, "cpu_percent": 0.1},
                {"pid": 2, "name": "pipewire", "memory_percent": 1.2, "cpu_percent": 0.4},
            ],
        )
        orchestrator.tools.register(mock_tool)

        res = orchestrator.handle("what is running on my computer")
        assert "Active processes include" in res.text
        assert "systemd" in res.text

    def test_system_info_conversational(self, orchestrator):
        mock_tool = MagicMock()
        mock_tool.name = "system.system_info"
        mock_tool.execute.return_value = ToolResult(
            success=True,
            data={
                "os": "Linux",
                "kernel": "6.11.0-generic",
                "architecture": "x86_64",
                "cpu_count": 16,
            },
        )
        orchestrator.tools.register(mock_tool)

        res = orchestrator.handle("what operating system am i on?")
        assert "Linux" in res.text
        assert "6.11.0-generic" in res.text
        assert "16 CPU cores" in res.text


# ==============================================================================
# Category C: Native Desktop Actions
# ==============================================================================
class TestCategoryCNativeDesktopActions:
    def test_open_url_action(self, orchestrator):
        with patch("webbrowser.open", return_value=True):
            res = orchestrator.handle("open https://github.com", auto_execute_actions=True)
            assert "Opening https://github.com." in res.text
            assert isinstance(res.action, OpenUrlAction)

    def test_open_popular_website_alias(self, orchestrator):
        with patch("webbrowser.open", return_value=True):
            res = orchestrator.handle("open youtube", auto_execute_actions=True)
            assert "Opening https://www.youtube.com." in res.text
            assert isinstance(res.action, OpenUrlAction)

    def test_open_folder_alias_documents(self, orchestrator, tmp_path):
        with (
            patch("subprocess.Popen") as mock_popen,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            docs_dir = tmp_path / "Documents"
            docs_dir.mkdir(parents=True, exist_ok=True)

            res = orchestrator.handle("open my Documents folder", auto_execute_actions=True)
            assert "Opening folder Documents" in res.text
            assert isinstance(res.action, OpenDirAction)
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "xdg-open"
            assert str(docs_dir) in cmd[1]

    def test_open_folder_alias_downloads(self, orchestrator, tmp_path):
        with (
            patch("subprocess.Popen") as mock_popen,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            dl_dir = tmp_path / "Downloads"
            dl_dir.mkdir(parents=True, exist_ok=True)

            res = orchestrator.handle("open downloads", auto_execute_actions=True)
            assert "Opening folder Downloads" in res.text
            assert isinstance(res.action, OpenDirAction)
            mock_popen.assert_called_once()

    def test_open_application_resolved(self, orchestrator):
        orchestrator.app_resolver.resolve.return_value = ApplicationResolution(
            requested_name="google chrome",
            canonical_name="Google Chrome",
            executable="/usr/bin/google-chrome-stable",
            desktop_entry=None,
            platform="Linux",
            installed=True,
            confidence=0.95,
        )
        orchestrator.app_resolver.launch.return_value = (True, "Opening Google Chrome.")

        res = orchestrator.handle("open google chrome", auto_execute_actions=True)
        assert res.text == "Opening Google Chrome."
        assert isinstance(res.action, OpenAppAction)

    def test_open_application_uninstalled_reports_clearly(self, orchestrator):
        orchestrator.app_resolver.resolve.return_value = ApplicationResolution(
            requested_name="photoshop",
            canonical_name="photoshop",
            executable=None,
            desktop_entry=None,
            platform="Linux",
            installed=False,
            confidence=0.0,
        )

        res = orchestrator.handle("open photoshop", auto_execute_actions=True)
        assert "Application 'photoshop' is not installed on this system." in res.text
        assert res.action is None

    def test_timer_action_execution(self, orchestrator):
        with patch("time.sleep") as mock_sleep:
            res = orchestrator.handle("set a timer for 5 seconds", auto_execute_actions=True)
            assert "Timer set for 5 seconds." in res.text
            assert "Time's up." in res.text
            assert isinstance(res.action, TimerAction)
            mock_sleep.assert_called_once_with(5.0)


# ==============================================================================
# Category D: Conversational Follow-up State
# ==============================================================================
class TestCategoryDConversationalFollowUp:
    def test_orchestrator_maintains_turn_history(self, orchestrator):
        assert len(orchestrator.history) == 0

        orchestrator.handle("hi")
        orchestrator.handle("how much memory is free?")

        assert len(orchestrator.history) == 2
        turn1 = orchestrator.history.turns[0]
        turn2 = orchestrator.history.turns[1]

        assert turn1.turn_id == 1
        assert turn1.user_query == "hi"
        assert turn1.intent_type == "GREETING"

        assert turn2.turn_id == 2
        assert turn2.user_query == "how much memory is free?"
        assert turn2.intent_type == "MEMORY_TOTAL"

    def test_follow_up_disk_space_query(self, orchestrator):
        mock_disk = MagicMock()
        mock_disk.name = "system.disk_usage"
        mock_disk.execute.return_value = ToolResult(
            success=True,
            data={"free_bytes": 100 * (1024**3), "total_bytes": 200 * (1024**3), "path": "/"},
        )
        orchestrator.tools.register(mock_disk)

        # Turn 1: Initial disk query
        res1 = orchestrator.handle("how much space is left on my laptop?")
        assert "100 GB free" in res1.text

        # Turn 2: Follow-up asking about space breakdown
        res2 = orchestrator.handle("what is taking up the most space?")
        assert res2.tool_result is not None
        assert "Primary storage is allocated" in res2.text or "100 GB free" in res2.text

    def test_follow_up_ram_query(self, orchestrator):
        mock_proc = MagicMock()
        mock_proc.name = "system.processes"
        mock_proc.execute.return_value = ToolResult(
            success=True,
            data=[{"pid": 55, "name": "ollama", "memory_percent": 14.5, "cpu_percent": 3.2}],
        )
        orchestrator.tools.register(mock_proc)

        # Turn 1: Memory total overview
        orchestrator.handle("how much memory do i have?")

        # Turn 2: Contextual follow-up asking which app is using the most
        res2 = orchestrator.handle("which app is using the most?")
        assert "ollama is currently using the most memory" in res2.text

    def test_history_bounded_capacity(self):
        history = ConversationHistory(max_turns=3)
        for i in range(5):
            history.add_turn(user_query=f"query {i}", intent_type="TEST", response_text=f"resp {i}")

        assert len(history) == 3
        assert history.turns[0].user_query == "query 2"
        assert history.turns[-1].user_query == "query 4"


# ==============================================================================
# Category E: Clarification Requests
# ==============================================================================
class TestCategoryEClarification:
    @pytest.mark.parametrize(
        ("prompt", "keyword"),
        [
            ("open it", "specify an application"),
            ("open this", "specify an application"),
            ("delete that", "specify the file or directory"),
            ("delete it", "specify the file or directory"),
            ("open the project", "Which project folder"),
            ("clean it up", "What would you like to clean up"),
            ("send this", "What would you like to send"),
            ("close it", "Which application or window"),
        ],
    )
    def test_ambiguous_requests_seek_clarification(self, orchestrator, prompt, keyword):
        res = orchestrator.handle(prompt)
        assert keyword in res.text
        assert res.action is None
        assert res.tool_result is None
        assert res.command_request is None


# ==============================================================================
# Category F: Invalid Requests
# ==============================================================================
class TestCategoryFInvalidRequests:
    @pytest.mark.parametrize(
        "query",
        [
            "set a timer for banana",
            "timer banana",
            "set a timer for",
            "timer",
        ],
    )
    def test_invalid_timer_duration_guides_user(self, orchestrator, query):
        res = orchestrator.handle(query)
        assert "Please specify a valid duration" in res.text
        assert res.action is None

    @pytest.mark.parametrize(
        "query",
        [
            "set a timer for 0 seconds",
            "timer 0s",
        ],
    )
    def test_zero_or_negative_timer_guides_user(self, orchestrator, query):
        res = orchestrator.handle(query)
        assert "Timer duration must be greater than zero" in res.text
        assert res.action is None

    def test_empty_or_whitespace_query_returns_empty(self, orchestrator):
        res = orchestrator.handle("   ")
        assert res.text == ""
        assert res.action is None


# ==============================================================================
# Category G: Safety Invariants & Execution Boundaries
# ==============================================================================
class TestCategoryGSafetyInvariants:
    def test_read_only_questions_never_execute_commands(self, orchestrator):
        with patch.object(orchestrator.router, "execute_command") as mock_exec:
            orchestrator.handle("how much space is left on my laptop?")
            orchestrator.handle("how much memory is free?")
            orchestrator.handle("what operating system am i on?")
            orchestrator.handle("what is using the most ram?")
            mock_exec.assert_not_called()

    def test_blocked_command_proposal_is_halted(self, orchestrator):
        orchestrator.router._provider = MockAssistantProvider("COMMAND: rm -rf /")
        res = orchestrator.handle("clean everything")

        assert res.is_blocked is True
        assert res.safety_assessment.level == RiskLevel.BLOCK
        assert "Blocked:" in res.text

    def test_modifying_command_proposal_requires_confirmation(self, orchestrator):
        orchestrator.router._provider = MockAssistantProvider("COMMAND: rm file.txt")
        res = orchestrator.handle("delete file.txt")

        assert res.requires_confirmation is True
        assert res.safety_assessment.level == RiskLevel.CONFIRM
        assert res.command_request is not None
        assert res.command_request.command_line == "rm file.txt"
