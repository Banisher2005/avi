"""Integration test for non-blocking concurrent runtime and streaming execution."""

import time
from unittest.mock import MagicMock

import pytest

from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher
from avi.agent.formatting import OperationalEventFormatter
from avi.agent.models import Plan, PlanStep
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.runtime import AgentRuntime
from avi.capabilities.models import BaseCapability, CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.config import Config
from avi.core.router import Router
from avi.memory.manager import MemoryManager
from avi.memory.retriever import MemoryRetriever
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.storage.database import Database


class SlowCapability(BaseCapability):
    name = "test.slow_work"
    description = "Simulate slow work"
    tags = ["test"]
    requires_confirmation = False

    def execute(self, **kwargs):
        # Sleep in small increments to allow cancellation/pause checks
        for _ in range(6):
            time.sleep(0.05)
        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Slow work finished: {kwargs.get('stage', 'default')}",
        )


@pytest.fixture
def integrated_runtime(tmp_path):
    config = Config()
    db = Database(db_path=tmp_path / "integration_runtime.db")
    retriever = MemoryRetriever(database=db)
    memory_mgr = MemoryManager(database=db)

    registry = CapabilityRegistry()
    registry.register(SlowCapability())

    events = EventDispatcher()

    agent_orch = AgentOrchestrator(
        registry=registry,
        database=db,
        memory_retriever=retriever,
        event_dispatcher=events,
    )

    router = MagicMock(spec=Router)
    router.check_fast_path.return_value = None

    assistant_orch = AssistantOrchestrator(
        config=config,
        router=router,
        database=db,
        memory=memory_mgr,
        agent_orchestrator=agent_orch,
    )

    runtime = AgentRuntime(
        config=config,
        router=router,
        assistant_orchestrator=assistant_orch,
        agent_orchestrator=agent_orch,
        events=events,
    )

    return runtime, agent_orch, events


def test_concurrent_interactive_runtime_non_blocking(integrated_runtime):
    runtime, agent_orch, events = integrated_runtime

    # Mock planner to return a 3-step slow plan with distinct arguments
    planner = MagicMock()
    plan = Plan(
        user_goal="Process heavy data pipeline",
        steps=[
            PlanStep(step_id=1, capability_name="test.slow_work", arguments={"stage": 1}, description="Phase 1"),
            PlanStep(step_id=2, capability_name="test.slow_work", arguments={"stage": 2}, description="Phase 2"),
            PlanStep(step_id=3, capability_name="test.slow_work", arguments={"stage": 3}, description="Phase 3"),
        ],
    )
    planner.create_plan.return_value = plan
    agent_orch.planner = planner
    agent_orch.can_handle = lambda q: "pipeline" in q

    collected_events = []
    events.subscribe(lambda ev: collected_events.append(ev))

    # 1. Dispatch long-running task -> must return immediately (non-blocking!)
    start_t = time.time()
    resp1 = runtime.dispatch("Process heavy data pipeline")
    dispatch_duration = time.time() - start_t

    # Must return virtually instantly (< 250ms), NOT blocking on execution
    assert dispatch_duration < 0.25
    assert resp1.is_background is True
    assert resp1.task is not None
    assert resp1.task.is_active()

    # 2. While task is actively running, dispatch interactive queries
    # Query 1: check what is running
    status_resp = runtime.dispatch("what are you doing?")
    assert "Process heavy data pipeline" in status_resp.text

    # Query 2: list tasks
    tasks_resp = runtime.dispatch("tasks")
    assert "#1" in tasks_resp.text
    assert "Process heavy data" in tasks_resp.text

    # Query 3: Fast-path / immediate greeting
    runtime.router.check_fast_path.return_value = "I'm here!"
    hi_resp = runtime.dispatch("hi")
    assert hi_resp.text == "I'm here!"
    assert hi_resp.is_background is False

    # 3. Wait for background task to complete
    resp1.task.worker_thread.join(timeout=5.0)
    assert resp1.task.status == TaskStatus.COMPLETED

    # 4. Verify formatted events had operational output
    formatted_lines = [OperationalEventFormatter.format_event(e) for e in collected_events]
    assert any("Plan ready" in line for line in formatted_lines)
    assert any("Phase 1" in line or "Executing action" in line for line in formatted_lines)


def test_concurrent_runtime_cancellation(integrated_runtime):
    runtime, agent_orch, events = integrated_runtime

    # Mock planner to return 5-step plan
    planner = MagicMock()
    plan = Plan(
        user_goal="Lengthy download operation",
        steps=[
            PlanStep(
                step_id=i,
                capability_name="test.slow_work",
                arguments={"chunk": i},
                description=f"Download chunk {i}",
            )
            for i in range(1, 6)
        ],
    )
    planner.create_plan.return_value = plan
    agent_orch.planner = planner
    agent_orch.can_handle = lambda q: True

    resp = runtime.dispatch("Lengthy download operation")
    assert resp.is_background is True
    task = resp.task
    assert task is not None

    # Let step 1 start
    time.sleep(0.05)

    # Cancel via runtime command
    cancel_resp = runtime.dispatch("cancel that")
    assert "Cancelled task" in cancel_resp.text
    assert task.status == TaskStatus.CANCELLED

    # Wait for thread to exit quickly
    task.worker_thread.join(timeout=3.0)
    assert not task.worker_thread.is_alive()
