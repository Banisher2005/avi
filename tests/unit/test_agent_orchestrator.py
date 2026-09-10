"""Unit tests for AgentOrchestrator lifecycle, loop guard, event dispatching, and error handling."""

from unittest.mock import MagicMock

import pytest

from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher, ProgressEventType
from avi.agent.executor import AgentExecutor
from avi.agent.models import Plan, PlanStep
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.memory.retriever import MemoryRetriever
from avi.storage.database import Database


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_agent.db"
    return Database(db_path=db_path)


@pytest.fixture
def memory_retriever(temp_db):
    return MemoryRetriever(database=temp_db)


@pytest.fixture
def mock_registry(tmp_path):
    registry = MagicMock(spec=CapabilityRegistry)
    screenshot_path = tmp_path / "screen.png"
    screenshot_path.write_text("screenshot data")

    catalog = [
        {
            "name": "desktop.screenshot",
            "description": "Capture screenshot",
            "tags": ["screenshot"],
        },
        {
            "name": "desktop.open_file",
            "description": "Open a local file",
            "tags": ["file"],
        },
        {
            "name": "filesystem.delete",
            "description": "Delete a file or directory",
            "tags": ["filesystem", "delete"],
        },
    ]
    registry.get_model_catalog.return_value = catalog

    def fake_execute(name, args=None, safety_engine=None, confirmed=False):
        if name == "filesystem.delete" and not confirmed:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.CONFIRMATION_REQUIRED,
                message="Are you sure you want to delete this file?",
            )
        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Executed {name}",
            data={"status": "ok", "path": str(screenshot_path)},
        )

    registry.execute_safe.side_effect = fake_execute
    return registry


def test_orchestrator_deterministic_plan(mock_registry, temp_db, memory_retriever):
    events = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe(lambda ev: events.append(ev))

    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
        event_dispatcher=dispatcher,
    )

    ctx = orchestrator.run("take a screenshot and open it")
    assert ctx.status == TaskStatus.COMPLETED
    assert len(ctx.steps) == 2
    assert ctx.steps[0].capability_name == "desktop.screenshot"
    assert ctx.steps[1].capability_name == "desktop.open_file"

    # Verify event stream
    event_types = [ev.event_type for ev in events]
    assert ProgressEventType.TASK_STARTED in event_types
    assert ProgressEventType.PLANNING in event_types
    assert ProgressEventType.CAPABILITY_SELECTED in event_types
    assert ProgressEventType.STEP_STARTED in event_types
    assert ProgressEventType.STEP_COMPLETED in event_types
    assert ProgressEventType.TASK_COMPLETED in event_types


def test_orchestrator_confirmation_pause(mock_registry, temp_db, memory_retriever):
    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
    )

    # Force a plan that requires confirmation
    planner = MagicMock(spec=AgentPlanner)
    plan = Plan(
        user_goal="delete sensitive directory",
        steps=[
            PlanStep(
                step_id=1,
                capability_name="filesystem.delete",
                arguments={"path": "/tmp/test_dir"},
                description="Delete test dir",
            )
        ],
        requires_confirmation=True,
        confirmation_prompt="Do you want to delete /tmp/test_dir?",
    )
    planner.create_plan.return_value = plan
    orchestrator.planner = planner

    # Unconfirmed run
    ctx = orchestrator.run("delete test dir", confirmed=False)
    assert ctx.status == TaskStatus.PAUSED_FOR_CONFIRMATION
    assert "delete" in ctx.final_response.lower()

    # Confirmed run
    ctx_confirmed = orchestrator.run("delete test dir", confirmed=True)
    assert ctx_confirmed.status == TaskStatus.COMPLETED


def test_orchestrator_loop_guard_protection(mock_registry, temp_db, memory_retriever):
    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
    )

    # Force a repeating plan that exceeds loop threshold
    planner = MagicMock(spec=AgentPlanner)
    plan = Plan(
        user_goal="repeated action",
        steps=[
            PlanStep(step_id=1, capability_name="desktop.screenshot", arguments={}),
            PlanStep(step_id=2, capability_name="desktop.screenshot", arguments={}),
            PlanStep(step_id=3, capability_name="desktop.screenshot", arguments={}),
            PlanStep(step_id=4, capability_name="desktop.screenshot", arguments={}),
        ],
    )
    planner.create_plan.return_value = plan
    orchestrator.planner = planner

    ctx = orchestrator.run("take repeating screenshots")
    assert ctx.status == TaskStatus.FAILED
    assert "loop" in ctx.final_response.lower() or "loop" in str(ctx.error_details).lower()


def test_orchestrator_memory_injection(mock_registry, temp_db, memory_retriever):
    memory_retriever.manager.remember("Default project is /home/user/code/avi", category="project")

    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
    )

    ctx = orchestrator.run("check my avi project files")
    assert len(ctx.memories) >= 1
    assert "Default project is /home/user/code/avi" in ctx.memories[0].content


def test_orchestrator_empty_prompt():
    orch = AgentOrchestrator()
    ctx = orch.run("")
    assert ctx.status == TaskStatus.COMPLETED
    assert ctx.final_response == ""


def test_orchestrator_clean_response_synthesis_no_leak(mock_registry, temp_db, memory_retriever):
    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
    )
    ctx = orchestrator.run("take a screenshot and open it")
    assert ctx.status == TaskStatus.COMPLETED
    assert "<think>" not in ctx.final_response
    assert "</think>" not in ctx.final_response
    assert "Traceback" not in ctx.final_response
    assert "{" not in ctx.final_response  # No raw JSON dump
    assert "Executed desktop.screenshot" in ctx.final_response or "Captured screenshot" in ctx.final_response or "Opened" in ctx.final_response


def test_orchestrator_replan_event_on_failure(mock_registry, temp_db, memory_retriever):
    events = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe(lambda ev: events.append(ev))

    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
        event_dispatcher=dispatcher,
    )

    # Force a failing step
    planner = MagicMock(spec=AgentPlanner)
    plan = Plan(
        user_goal="fail task",
        steps=[
            PlanStep(step_id=1, capability_name="filesystem.delete", arguments={"path": "/nonexistent"})
        ],
    )
    planner.create_plan.return_value = plan
    orchestrator.planner = planner

    # Make execute fail
    mock_registry.execute_safe.side_effect = None
    mock_registry.execute_safe.return_value = CapabilityResult(
        success=False,
        status=ExecutionStatus.FAILED,
        error="Permission denied",
    )

    ctx = orchestrator.run("fail task", confirmed=True)
    assert ctx.status == TaskStatus.FAILED
    assert ctx.replan_count >= 1
    event_types = [ev.event_type for ev in events]
    assert ProgressEventType.REPLANNING in event_types


def test_orchestrator_play_latest_vid():
    planner = AgentPlanner()
    plan = planner.create_plan("play the latest vid")
    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.steps[0].capability_name == "web.youtube.search_results"
    assert plan.steps[1].capability_name == "desktop.open_url"
    assert plan.steps[1].pipe_from_step == 1
    assert plan.steps[1].pipe_arg_name == "url"


def test_orchestrator_open_youtube_and_play():
    planner = AgentPlanner()
    plan = planner.create_plan("open youtube and play the latest video")
    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.steps[0].capability_name == "web.youtube.search_results"
    assert plan.steps[1].capability_name == "desktop.open_url"


def test_orchestrator_play_mkbhd_video():
    planner = AgentPlanner()
    plan = planner.create_plan("play the latest video by mkbhd")
    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.steps[0].arguments.get("query") == "mkbhd latest"
    assert plan.steps[1].capability_name == "desktop.open_url"


def test_orchestrator_find_newest_pdf_in_downloads():
    planner = AgentPlanner()
    plan = planner.create_plan("find the newest PDF in Downloads and open it")
    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.steps[0].capability_name == "filesystem.search"
    assert plan.steps[0].arguments.get("path") == "~/Downloads"
    assert plan.steps[0].arguments.get("extension") == "pdf"
    assert plan.steps[1].capability_name == "desktop.open_file"
    assert plan.steps[1].pipe_from_step == 1


def test_orchestrator_screenshot_report_location(mock_registry, temp_db, memory_retriever):
    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
    )
    ctx = orchestrator.run("take a screenshot and tell me where it was saved")
    assert ctx.status == TaskStatus.COMPLETED
    assert ctx.is_terminal()
    assert "screen.png" in ctx.final_response
    assert "saved it to" in ctx.final_response.lower()


def test_orchestrator_step_timeout_bounded_execution():
    """Verify that a slow or hanging capability step is cleanly bounded by step_timeout."""
    import time
    registry = MagicMock(spec=CapabilityRegistry)

    def hanging_call(*args, **kwargs):
        time.sleep(2.0)
        return CapabilityResult(success=True, message="done")

    registry.execute_safe.side_effect = hanging_call

    executor = AgentExecutor(
        registry=registry,
        step_timeout=0.2,
        verification_timeout=0.1,
    )

    plan = Plan(
        user_goal="hanging step test",
        steps=[
            PlanStep(step_id=1, capability_name="slow.action", arguments={})
        ],
    )

    t0 = time.perf_counter()
    res = executor.execute_plan(plan)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0  # Proves execution did not hang for 2.0s
    assert res.success is False
    assert "timed out" in (res.error or "").lower()


def test_orchestrator_terminal_state_guarantee(mock_registry, temp_db, memory_retriever):
    events = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe(lambda ev: events.append(ev))

    orchestrator = AgentOrchestrator(
        registry=mock_registry,
        database=temp_db,
        memory_retriever=memory_retriever,
        event_dispatcher=dispatcher,
    )

    # Empty prompt
    ctx_empty = orchestrator.run("")
    assert ctx_empty.is_terminal()

    # Successful prompt
    ctx_succ = orchestrator.run("take a screenshot and open it")
    assert ctx_succ.is_terminal()
    assert ctx_succ.status == TaskStatus.COMPLETED

    # Unhandled prompt
    ctx_unhandled = orchestrator.run("xyzzy unhandled random prompt")
    assert ctx_unhandled.is_terminal()

    # Verify that terminal event matches final status
    term_events = [ev for ev in events if ev.event_type in (
        ProgressEventType.TASK_COMPLETED,
        ProgressEventType.TASK_FAILED,
        ProgressEventType.PAUSED_FOR_CONFIRMATION,
    )]
    assert len(term_events) >= 2

