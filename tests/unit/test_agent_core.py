"""Unit tests for Phase 15 Agent Core: planning, task state, observe-act-verify loop, and memory integration."""

from unittest.mock import MagicMock, patch

import pytest

from avi.agent.executor import AgentExecutor
from avi.agent.models import Plan, PlanStep
from avi.agent.planner import AgentPlanner
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.config import Config
from avi.memory.manager import MemoryManager
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.storage.database import Database


class TestCompoundPlanning:
    """Test AgentPlanner compound goal planning."""

    def test_plan_youtube_search_and_play(self):
        planner = AgentPlanner()
        plan = planner.create_plan("open youtube, search for mkbhd, and play the latest video")
        assert plan is not None
        assert len(plan.steps) >= 2
        assert plan.steps[0].capability_name in ("web.youtube.search", "web.youtube.search_results")
        assert plan.steps[0].arguments.get("query") == "mkbhd"
        assert plan.steps[1].capability_name == "desktop.open_url"
        assert plan.steps[1].pipe_from_step == 1
        assert plan.steps[1].pipe_arg_name == "url"

    def test_plan_screenshot_and_open(self):
        planner = AgentPlanner()
        plan = planner.create_plan("take a screenshot and open it")
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].capability_name == "desktop.screenshot"
        assert plan.steps[1].capability_name == "desktop.open_file"
        assert plan.steps[1].pipe_from_step == 1
        assert plan.steps[1].pipe_arg_name == "path"


class TestAgentExecutionLoop:
    """Test Observe -> Act -> Verify execution loop and TaskState updates."""

    def test_observe_act_verify_success(self, tmp_path):
        mock_registry = MagicMock(spec=CapabilityRegistry)
        screenshot_path = tmp_path / "screen.png"
        screenshot_path.write_text("test image")

        def fake_exec_safe(name, args=None, safety_engine=None, confirmed=False):
            if name == "desktop.screenshot":
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": str(screenshot_path)},
                    message="Screenshot taken",
                )
            if name == "desktop.open_file":
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": args["path"]},
                    message="Opened file",
                )
            return CapabilityResult(success=False, status=ExecutionStatus.FAILED)

        mock_registry.execute_safe.side_effect = fake_exec_safe
        db = Database(db_path=tmp_path / "task_history.db")
        executor = AgentExecutor(registry=mock_registry, database=db)

        plan = Plan(
            user_goal="capture and open",
            steps=[
                PlanStep(step_id=1, capability_name="desktop.screenshot"),
                PlanStep(
                    step_id=2,
                    capability_name="desktop.open_file",
                    pipe_from_step=1,
                    pipe_arg_name="path",
                ),
            ],
        )

        res = executor.execute_plan(plan)
        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.task_state.status == "success"
        assert len(res.task_state.completed_steps) == 2
        assert res.task_state.pending_steps == []
        assert res.verification_duration_ms >= 0.0

        # Verify task history was recorded in database
        tasks = db.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].goal == "capture and open"


class TestOrchestratorPhase15Integration:
    """Test AssistantOrchestrator with Database, MemoryManager, and latency metrics."""

    @pytest.fixture
    def orchestrator(self, tmp_path):
        cfg = Config()
        db = Database(db_path=tmp_path / "orch_test.db")
        mem = MemoryManager(database=db)
        return AssistantOrchestrator(config=cfg, database=db, memory=mem)

    def test_memory_intent_execution_and_metrics(self, orchestrator):
        res = orchestrator.handle("remember that Chrome is my preferred browser")
        assert "Chrome" in res.text and "preferred browser" in res.text
        assert res.metrics.memory_duration_ms is not None
        assert res.metrics.memory_duration_ms >= 0.0
        assert res.metrics.routing_duration_ms is not None

        # Recall
        recall_res = orchestrator.handle("what do you remember about browser?")
        assert "Chrome is my preferred browser" in recall_res.text

    @patch("webbrowser.open")
    def test_open_url_resolves_preferred_browser(self, mock_open, orchestrator):
        # Set preferred browser in memory
        orchestrator.memory.set_preferred_browser("chrome")

        with patch("subprocess.Popen") as mock_popen:
            # We mock resolver to indicate chrome is installed
            with patch.object(orchestrator.app_resolver, "resolve") as mock_resolve:
                mock_app = MagicMock()
                mock_app.installed = True
                mock_app.executable = "/usr/bin/google-chrome"
                mock_app.canonical_name = "Google Chrome"
                mock_resolve.return_value = mock_app

                res = orchestrator.handle("open chatgpt")
                assert "Opening https://chatgpt.com in Google Chrome." in res.text
                mock_popen.assert_called_once()
                args, kwargs = mock_popen.call_args
                assert "chrome" in args[0][0]
                assert args[0][1] == "https://chatgpt.com"

    def test_fast_paths_bypass_llm(self, orchestrator):
        # Greetings must be instant with 0 LLM calls
        res_hi = orchestrator.handle("hiiiiii")
        assert "Hello!" in res_hi.text
        assert res_hi.metrics.routing_duration_ms is not None
        assert res_hi.metrics.total_duration_ms < 100.0  # < 100ms
