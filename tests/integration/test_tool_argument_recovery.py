"""Integration tests verifying autonomous tool argument binding, recovery, and prevention of empty parameter failures."""

from unittest.mock import MagicMock

from avi.agent.context import TaskStatus
from avi.agent.dynamic_loop import DynamicAgentLoop
from avi.agent.events import EventDispatcher, ProgressEventType
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry
from avi.providers.models import ToolCall
from avi.storage.database import Database


class TestToolArgumentRecoveryIntegration:
    """End-to-end integration tests for bug reproduction and prevention."""

    def test_planner_resolves_brave_youtube_search(self):
        """Planner directly recognizes 'brave open youtube and search yeah jaron' as a YouTube search."""
        planner = AgentPlanner()

        plan = planner.create_plan("brave open youtube and search yeah jaron")
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].capability_name == "web.youtube.search"
        assert plan.steps[0].arguments["query"] == "yeah jaron"
        assert "path" not in plan.steps[0].arguments

    def test_dynamic_loop_executes_brave_youtube_search_cleanly(self):
        """DynamicAgentLoop handles 'brave open youtube and search yeah jaron' without filesystem path errors."""
        registry = create_default_capability_registry()
        executed_tools = []

        def mock_execute_safe(name, args=None, safety_engine=None, confirmed=False):
            executed_tools.append((name, dict(args or {})))
            if name == "web.youtube.search":
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    message="Opened YouTube search for yeah jaron",
                    data={"query": "yeah jaron", "url": "https://www.youtube.com/results?search_query=yeah+jaron"},
                )
            return CapabilityResult(success=True, status=ExecutionStatus.SUCCESS)

        registry.execute_safe = mock_execute_safe
        loop = DynamicAgentLoop(registry=registry)

        res = loop.run("brave open youtube and search yeah jaron")
        assert res.success
        assert res.status == TaskStatus.COMPLETED
        assert len(executed_tools) == 1
        tool_name, tool_args = executed_tools[0]
        assert tool_name in ("web.youtube.search", "desktop.open_url")
        assert "path" not in tool_args
        # Verify no open_file with empty path was ever called
        assert not any(t == "desktop.open_file" for t, _ in executed_tools)

    def test_orchestrator_handles_brave_youtube_request_without_duplicates(self, tmp_path):
        """Orchestrator dispatches and executes without duplicate error messages."""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        registry = create_default_capability_registry()
        dispatcher = EventDispatcher()
        events = []
        dispatcher.subscribe(lambda ev: events.append(ev))

        def mock_execute_safe(name, args=None, safety_engine=None, confirmed=False):
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="Executed YouTube search",
                data={"query": "yeah jaron"},
            )

        registry.execute_safe = mock_execute_safe
        orchestrator = AgentOrchestrator(
            registry=registry,
            database=db,
            event_dispatcher=dispatcher,
        )

        ctx = orchestrator.run("brave open youtube and search yeah jaron")
        assert ctx.status == TaskStatus.COMPLETED

        # Check for duplicate terminal events
        terminal_failures = [e for e in events if e.event_type in (ProgressEventType.TASK_FAILED, ProgressEventType.GOAL_FAILED)]
        assert len(terminal_failures) == 0

        terminal_completions = [e for e in events if e.event_type == ProgressEventType.TASK_COMPLETED]
        assert len(terminal_completions) <= 1

    def test_dynamic_loop_never_fabricates_empty_path(self):
        """When an unrecognized request with 'open' occurs, never guess path=''."""
        registry = create_default_capability_registry()
        loop = DynamicAgentLoop(registry=registry)
        obs = MagicMock()

        action = loop._select_next_action(
            goal="open something unknown",
            observation=obs,
            available_tools=[],
            step_records=[],
            context_metadata={},
        )
        if isinstance(action, ToolCall) and action.name == "desktop.open_file":
            # If open_file is selected, path must NOT be empty
            path_val = action.arguments.get("path")
            assert path_val is not None
            assert str(path_val).strip() != ""
            assert path_val != "<UNRESOLVED_ARGUMENT>"
