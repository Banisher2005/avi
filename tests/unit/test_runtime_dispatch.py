"""Unit tests for non-blocking AgentRuntime input dispatch and control commands."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from avi.agent.context import OrchestrationContext, TaskStatus
from avi.agent.events import EventDispatcher
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.runtime import AgentRuntime
from avi.agent.task_registry import RuntimeTask, RuntimeTaskRegistry
from avi.config import Config
from avi.core.router import Router
from avi.orchestrator.orchestrator import AssistantOrchestrator


@pytest.fixture
def mock_runtime():
    config = Config()
    router = MagicMock(spec=Router)
    router.check_fast_path.return_value = None

    assistant_orch = MagicMock(spec=AssistantOrchestrator)
    agent_orch = MagicMock(spec=AgentOrchestrator)
    agent_orch.can_handle.return_value = False

    registry = RuntimeTaskRegistry()
    dispatcher = EventDispatcher()

    runtime = AgentRuntime(
        config=config,
        router=router,
        assistant_orchestrator=assistant_orch,
        agent_orchestrator=agent_orch,
        task_registry=registry,
        events=dispatcher,
    )
    return runtime, router, assistant_orch, agent_orch, registry


def test_dispatch_fast_path(mock_runtime):
    runtime, router, _, _, _ = mock_runtime
    router.check_fast_path.return_value = "Hello! How can I help you today?"

    resp = runtime.dispatch("hi")
    assert resp.is_background is False
    assert resp.text == "Hello! How can I help you today?"


def test_dispatch_tasks_command(mock_runtime):
    runtime, _, _, _, registry = mock_runtime
    registry.register(RuntimeTask(task_id="t1", goal="Organize files"))

    resp = runtime.dispatch("tasks")
    assert resp.is_background is False
    assert "Active & Recent Tasks:" in resp.text
    assert "Organize files" in resp.text


def test_dispatch_what_are_you_doing(mock_runtime):
    runtime, _, _, _, registry = mock_runtime

    # 1. No active tasks
    resp1 = runtime.dispatch("what are you doing?")
    assert "not currently running any background tasks" in resp1.text

    # 2. One active task
    t1 = RuntimeTask(task_id="t1", goal="Analyze benchmark data")
    registry.register(t1)
    resp2 = runtime.dispatch("what are you doing?")
    assert "Analyze benchmark data" in resp2.text


def test_dispatch_pause_and_resume(mock_runtime):
    runtime, _, _, agent_orch, registry = mock_runtime
    t1 = RuntimeTask(task_id="t1", goal="Deep code search")
    registry.register(t1)

    # Pause
    resp_pause = runtime.dispatch("pause that")
    assert "Paused task: Deep code search." in resp_pause.text
    assert t1.status == TaskStatus.PAUSED
    agent_orch.pause_task.assert_called_with("t1")

    # Mock resume_task return
    resume_ctx = OrchestrationContext(user_prompt="Deep code search", status=TaskStatus.RUNNING)
    agent_orch.resume_task.return_value = resume_ctx

    # Resume
    resp_resume = runtime.dispatch("resume that")
    assert "Resumed task" in resp_resume.text
    assert "Deep code search" in resp_resume.text
    assert t1.status == TaskStatus.RUNNING


def test_dispatch_cancel(mock_runtime):
    runtime, _, _, agent_orch, registry = mock_runtime
    t1 = RuntimeTask(task_id="t1", goal="Batch file converter")
    registry.register(t1)

    resp_cancel = runtime.dispatch("cancel task")
    assert "Cancelled task: Batch file converter." in resp_cancel.text
    assert t1.status == TaskStatus.CANCELLED
    agent_orch.cancel_task.assert_called_with("t1")


def test_dispatch_ambiguous_cancel(mock_runtime):
    runtime, _, _, _, registry = mock_runtime
    t1 = RuntimeTask(task_id="t1", goal="Download file A")
    t2 = RuntimeTask(task_id="t2", goal="Download file B")
    registry.register(t1)
    registry.register(t2)

    resp = runtime.dispatch("stop download")
    assert resp.clarification_needed is True
    assert "Multiple tasks are running" in resp.text


def test_dispatch_background_task_spawns_worker(mock_runtime):
    runtime, _, _, agent_orch, registry = mock_runtime
    agent_orch.can_handle.return_value = True

    # Setup mock agent_orch.run to return a completed context
    def fake_run(prompt, **kwargs):
        ctx = OrchestrationContext(user_prompt=prompt, task_id="test_bg_id")
        ctx.status = TaskStatus.COMPLETED
        ctx.final_response = "Background work done."
        return ctx

    agent_orch.run.side_effect = fake_run

    resp = runtime.dispatch("complex multi-step operation")
    assert resp.is_background is True
    assert resp.task is not None
    assert "◉" in resp.task.format_badge() or resp.task.goal == "complex multi-step operation"

    # Wait for background thread to complete
    resp.task.worker_thread.join(timeout=2.0)
    assert resp.task.status == TaskStatus.COMPLETED
    assert resp.task.result == "Background work done."


def test_dispatch_resource_conflict(mock_runtime):
    runtime, _, _, agent_orch, registry = mock_runtime
    agent_orch.can_handle.return_value = True

    # Register task holding Downloads folder lock
    dl_path = str(Path.home() / "Downloads")
    t1 = RuntimeTask(
        task_id="t1",
        goal="organize downloads",
        resources={f"filesystem:{dl_path}"},
        is_write=True,
    )
    registry.resources.acquire("t1", {f"filesystem:{dl_path}"}, is_write=True)
    registry.register(t1)

    # Dispatch second mutating request targeting downloads
    resp = runtime.dispatch("clean up downloads folder")
    assert resp.is_background is False
    assert "cannot start yet because another task is actively modifying" in resp.text
