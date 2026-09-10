"""Integration tests verifying end-to-end adaptive recovery, replanning, and confirmation workflows."""

from unittest.mock import MagicMock

from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher, ProgressEventType
from avi.agent.models import Plan, PlanStep
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.storage.database import Database


class TestAdaptiveRecoveryIntegration:
    """Test full agent recovery loops with dynamic replanning."""

    def test_end_to_end_file_not_found_recovers_via_search(self, tmp_path):
        # Create a real file in a subfolder
        doc_dir = tmp_path / "documents"
        doc_dir.mkdir()
        target_file = doc_dir / "financial_report.pdf"
        target_file.write_text("Q4 Report Content")

        events = []
        dispatcher = EventDispatcher()
        dispatcher.subscribe(lambda ev: events.append(ev))

        mock_registry = MagicMock(spec=CapabilityRegistry)

        def mock_execute_safe(cap_name, args=None, safety_engine=None, confirmed=False):
            args = args or {}
            if cap_name == "filesystem.search":
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"results": [{"path": str(target_file)}], "path": str(target_file)},
                )
            elif cap_name in ("desktop.open_file", "open_file"):
                p = args.get("path", "")
                if p == str(target_file):
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Opened {target_file.name}",
                        data={"path": p},
                    )
                return CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error=f"File does not exist: {p}",
                )
            return CapabilityResult(success=True, status=ExecutionStatus.SUCCESS)

        mock_registry.execute_safe.side_effect = mock_execute_safe

        db = Database(db_path=str(tmp_path / "test.db"))
        orchestrator = AgentOrchestrator(
            registry=mock_registry,
            database=db,
            event_dispatcher=dispatcher,
        )

        # Initial plan assumes file is in ~/Downloads/financial_report.pdf (fails)
        planner = MagicMock(spec=AgentPlanner)
        initial_plan = Plan(
            user_goal="open financial_report.pdf",
            steps=[
                PlanStep(
                    step_id=1,
                    capability_name="desktop.open_file",
                    arguments={"path": "~/Downloads/financial_report.pdf"},
                    description="Open financial_report.pdf in Downloads",
                )
            ],
        )
        planner.create_plan.return_value = initial_plan
        real_planner = AgentPlanner()
        planner.replan.side_effect = real_planner.replan
        orchestrator.planner = planner

        ctx = orchestrator.run("open financial_report.pdf", confirmed=True)

        assert ctx.status == TaskStatus.COMPLETED
        assert ctx.replan_count == 1
        event_types = [ev.event_type for ev in events]
        assert ProgressEventType.FAILURE_DIAGNOSED in event_types
        assert ProgressEventType.REPLANNING in event_types
        assert ProgressEventType.PLAN_ADAPTED in event_types
        assert ProgressEventType.TASK_COMPLETED in event_types

    def test_end_to_end_browser_stale_element_reobserves_and_succeeds(self, tmp_path):
        events = []
        dispatcher = EventDispatcher()
        dispatcher.subscribe(lambda ev: events.append(ev))

        mock_registry = MagicMock(spec=CapabilityRegistry)
        call_counts = {"click": 0, "observe": 0}

        def mock_execute_safe(cap_name, args=None, safety_engine=None, confirmed=False):
            if cap_name == "browser.observe":
                call_counts["observe"] += 1
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={
                        "url": "https://example.com/checkout",
                        "interactive_elements": [{"id": 10, "text": "Pay"}],
                    },
                )
            elif cap_name == "browser.click":
                call_counts["click"] += 1
                if call_counts["click"] <= 2:
                    # Initial attempt and executor immediate retry both fail due to stale element
                    return CapabilityResult(
                        success=False,
                        status=ExecutionStatus.FAILED,
                        error="Node is detached from document (stale element)",
                    )
                # Subsequent click after adaptive replan (re-observation) succeeds
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    message="Clicked payment button",
                    data={"clicked": True, "selector": "#pay-btn"},
                )
            return CapabilityResult(success=True, status=ExecutionStatus.SUCCESS)

        mock_registry.execute_safe.side_effect = mock_execute_safe

        db = Database(db_path=str(tmp_path / "test.db"))
        orchestrator = AgentOrchestrator(
            registry=mock_registry,
            database=db,
            event_dispatcher=dispatcher,
        )

        planner = MagicMock(spec=AgentPlanner)
        initial_plan = Plan(
            user_goal="click pay button",
            steps=[
                PlanStep(
                    step_id=1,
                    capability_name="browser.click",
                    arguments={"selector": "#pay-btn"},
                    description="Click pay button",
                )
            ],
        )
        planner.create_plan.return_value = initial_plan
        real_planner = AgentPlanner()
        planner.replan.side_effect = real_planner.replan
        orchestrator.planner = planner

        ctx = orchestrator.run("click pay button", confirmed=True)

        assert ctx.status == TaskStatus.COMPLETED
        assert ctx.replan_count == 1
        assert call_counts["observe"] >= 1
        assert call_counts["click"] >= 3
        event_types = [ev.event_type for ev in events]
        assert ProgressEventType.FAILURE_DIAGNOSED in event_types
        assert ProgressEventType.PLAN_ADAPTED in event_types

    def test_confirmation_pause_and_resume_preserves_context(self, tmp_path):
        mock_registry = MagicMock(spec=CapabilityRegistry)
        executed_steps = []
        screen_file = tmp_path / "screen.png"
        screen_file.write_text("fake screenshot")

        def mock_execute_safe(cap_name, args=None, safety_engine=None, confirmed=False):
            if cap_name == "desktop.screenshot":
                executed_steps.append("screenshot")
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    message="Screenshot captured",
                    data={"path": str(screen_file)},
                )
            elif cap_name == "desktop.trash_file":
                executed_steps.append("trash")
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    message="File deleted",
                )
            return CapabilityResult(success=True, status=ExecutionStatus.SUCCESS)

        mock_registry.execute_safe.side_effect = mock_execute_safe

        db = Database(db_path=str(tmp_path / "test.db"))
        orchestrator = AgentOrchestrator(registry=mock_registry, database=db)

        # Plan with confirmation requirement
        plan = Plan(
            user_goal="delete sensitive screenshot",
            steps=[
                PlanStep(
                    step_id=1,
                    capability_name="desktop.screenshot",
                    arguments={},
                    description="Capture",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="desktop.trash_file",
                    arguments={"path": str(screen_file)},
                    description="Trash",
                ),
            ],
            requires_confirmation=True,
            confirmation_prompt=f"Are you sure you want to delete {screen_file}?",
        )
        planner = MagicMock(spec=AgentPlanner)
        planner.create_plan.return_value = plan
        orchestrator.planner = planner

        # Run 1: Unconfirmed -> Pauses
        ctx1 = orchestrator.run("delete sensitive screenshot", confirmed=False)
        assert ctx1.status == TaskStatus.PAUSED_FOR_CONFIRMATION
        assert "Are you sure" in ctx1.final_response
        assert len(executed_steps) == 0

        # Run 2: Confirmed -> Completes
        ctx2 = orchestrator.run("delete sensitive screenshot", confirmed=True)
        assert ctx2.status == TaskStatus.COMPLETED
        assert "screenshot" in executed_steps
        assert "trash" in executed_steps

    def test_exhausted_replans_reports_clean_failure(self, tmp_path):
        mock_registry = MagicMock(spec=CapabilityRegistry)
        mock_registry.execute_safe.return_value = CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error="Fatal system IO error",
        )

        db = Database(db_path=str(tmp_path / "test.db"))
        orchestrator = AgentOrchestrator(registry=mock_registry, database=db)

        plan = Plan(
            user_goal="open missing file",
            steps=[
                PlanStep(
                    step_id=1,
                    capability_name="desktop.open_file",
                    arguments={"path": "/invalid"},
                    description="Open file",
                )
            ],
        )
        planner = MagicMock(spec=AgentPlanner)
        planner.create_plan.return_value = plan
        planner.replan.return_value = None
        orchestrator.planner = planner

        ctx = orchestrator.run("open missing file", confirmed=True)
        assert ctx.status == TaskStatus.FAILED
        assert "Fatal system IO error" in ctx.final_response
