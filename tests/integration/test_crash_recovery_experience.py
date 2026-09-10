"""Integration tests for Phase 7: crash-safe recovery across orchestrator instances,
experience learning across sessions, and conversational continuation.
"""

from unittest.mock import MagicMock

from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher, ProgressEventType
from avi.agent.experience import ExperienceStore
from avi.agent.models import PlanStep, StepStatus
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.storage.database import Database
from avi.storage.models import DurableTaskRecord


def test_crash_recovery_across_instances(tmp_path):
    """Simulate a task paused midway, process termination, and a fresh orchestrator
    resuming the task with environment reconciliation, skipping satisfied steps.
    """
    db_file = tmp_path / "shared_agent.db"
    db1 = Database(db_path=db_file)

    created_dir = tmp_path / "restored_dir"

    # Step 1: filesystem.create_directory -> already executed in environment
    created_dir.mkdir()
    step1 = PlanStep(
        step_id=1,
        capability_name="filesystem.create_directory",
        arguments={"path": str(created_dir)},
        status=StepStatus.SUCCESS,
        verified=True,
    )

    # Step 2: filesystem.write_file -> pending
    dest_file = created_dir / "summary.txt"
    step2 = PlanStep(
        step_id=2,
        capability_name="filesystem.write_file",
        arguments={"path": str(dest_file), "content": "Autonomous execution complete"},
        status=StepStatus.PENDING,
    )

    # Create a recorded paused task in db1
    task_id = "task-crash-resume-99"
    durable_rec = DurableTaskRecord(
        task_id=task_id,
        goal="Create directory and write summary",
        normalized_goal="create directory and write summary",
        status="paused",
        current_step_index=1,
        total_steps=2,
        plan_data={
            "context": {
                "task_id": task_id,
                "user_prompt": "Create directory and write summary",
                "status": "paused",
                "current_step_index": 1,
                "steps": [step1.to_dict(), step2.to_dict()],
            }
        },
    )
    db1.save_durable_task(durable_rec)

    # Simulate process shutdown: close db1, discard orchestrator 1
    db1.close()

    # Process restart: fresh Database and fresh AgentOrchestrator
    db2 = Database(db_path=db_file)
    events = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe(lambda ev: events.append(ev))

    mock_registry = MagicMock(spec=CapabilityRegistry)
    executed_capabilities = []

    def fake_execute(name, args=None, safety_engine=None, confirmed=False):
        executed_capabilities.append(name)
        if name == "filesystem.write_file":
            dest_file.write_text(args.get("content", ""), encoding="utf-8")
        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Executed {name}",
            data={"status": "ok", "path": str(dest_file)},
        )

    mock_registry.execute_safe.side_effect = fake_execute

    orchestrator2 = AgentOrchestrator(
        registry=mock_registry,
        database=db2,
        event_dispatcher=dispatcher,
    )

    # Resume the interrupted task
    resumed_ctx = orchestrator2.resume_task(task_id)

    assert resumed_ctx.status == TaskStatus.COMPLETED
    # Verify that step 1 was skipped / not re-executed, only step 2 was dispatched
    assert "filesystem.write_file" in executed_capabilities
    assert "filesystem.create_directory" not in executed_capabilities

    # Verify event stream
    ev_types = [e.event_type for e in events]
    assert ProgressEventType.TASK_RESUMED in ev_types
    assert ProgressEventType.GOAL_RECONCILED in ev_types
    assert ProgressEventType.GOAL_COMPLETED in ev_types

    # Verify database record updated to completed
    final_rec = db2.get_durable_task(task_id)
    assert final_rec.status == "completed"


def test_experience_strategy_learning_across_sessions(tmp_path):
    """Simulate strategy learning: session 1 records failure of strategy A;
    session 2 learns from experience and avoids strategy A.
    """
    db_file = tmp_path / "experience_learning.db"
    db_session1 = Database(db_path=db_file)
    exp_store1 = ExperienceStore(db=db_session1)

    # Session 1: records failure with strategy 'brute_force_download'
    exp_store1.record_outcome(
        goal="download annual finance dataset",
        strategy_name="brute_force_download",
        capability_used="desktop.network_download",
        success=False,
        failure_category="timeout",
    )
    db_session1.close()

    # Session 2: brand new process and orchestrator
    db_session2 = Database(db_path=db_file)
    exp_store2 = ExperienceStore(db=db_session2)

    # Query strategies for same or similar goal
    failed_strats = exp_store2.get_failed_strategies("download annual finance dataset")
    assert "brute_force_download" in failed_strats

    # Recommendation favors non-failing alternative
    chosen = exp_store2.recommend_strategy(
        "download annual finance dataset",
        available_strategies=["brute_force_download", "authenticated_browser_download"],
    )
    assert chosen == "authenticated_browser_download"


def test_conversational_continuation_flow(tmp_path):
    """Verify conversational continuation: Turn 1 creates an artifact, Turn 2 resolves 'it' to that artifact."""
    db = Database(db_path=tmp_path / "continuation.db")
    screenshot_file = tmp_path / "screenshot_2026.png"
    screenshot_file.write_text("image bytes", encoding="utf-8")

    mock_registry = MagicMock(spec=CapabilityRegistry)
    opened_files = []

    def fake_execute(name, args=None, safety_engine=None, confirmed=False):
        if name == "desktop.screenshot":
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="Screenshot saved",
                data={"path": str(screenshot_file)},
            )
        elif name == "desktop.open_file":
            opened_files.append(args.get("path") or args.get("file"))
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=f"Opened {args}",
            )
        return CapabilityResult(success=True, status=ExecutionStatus.SUCCESS)

    mock_registry.execute_safe.side_effect = fake_execute

    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=db,
    )

    # Turn 1: Capture screenshot
    ctx1 = orchestrator.run("take a screenshot")
    assert ctx1.status == TaskStatus.COMPLETED
    assert ctx1.steps[0].capability_name == "desktop.screenshot"

    # Turn 2: Follow-up command "now open it"
    # Pass metadata with last_artifact
    last_artifact = ctx1.steps[0].artifact_path or (ctx1.steps[0].output_data or {}).get("path")
    planner = AgentPlanner()
    resolved = planner.resolve_continuation("now open it", context={"last_artifact": last_artifact})
    assert str(screenshot_file) in resolved

    # Execute Turn 2
    ctx2 = orchestrator.run(resolved)
    assert ctx2.status == TaskStatus.COMPLETED
    assert "desktop.open_file" in [s.capability_name for s in ctx2.steps]
    assert str(screenshot_file) in opened_files
