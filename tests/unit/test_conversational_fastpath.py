"""Unit and contract tests for conversational fast-path and non-blocking interaction."""

import time
from unittest.mock import MagicMock

import pytest

from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher
from avi.agent.models import Plan, PlanStep
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
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
        for _ in range(10):
            time.sleep(0.05)
        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message="Slow work finished.",
        )


@pytest.fixture
def runtime_fixture(tmp_path):
    config = Config()
    db = Database(db_path=tmp_path / "test_conv.db")
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

    return runtime, agent_orch, assistant_orch


def test_conversational_greetings_and_intents_direct(runtime_fixture):
    runtime, agent_orch, assistant_orch = runtime_fixture

    # Spy on planner
    planner = MagicMock(spec=AgentPlanner)
    agent_orch.planner = planner

    queries = [
        "hello",
        "hi",
        "hey",
        "how are you?",
        "what can you do?",
        "how much RAM is free?",
        "what is my RAM usage?",
    ]

    for q in queries:
        t0 = time.perf_counter()
        resp = runtime.dispatch(q)
        duration_ms = (time.perf_counter() - t0) * 1000.0

        # Must have a real, non-empty response
        assert resp is not None
        assert resp.text is not None
        assert len(resp.text.strip()) > 0
        assert resp.is_background is False

        # Must execute in < 50ms (deterministic direct path)
        assert duration_ms < 50.0

        # Must NOT create an agent task in registry
        assert len(runtime.task_registry.active_tasks()) == 0

        # Must NOT call agent planner or background executor
        assert not planner.create_plan.called


def test_non_blocking_conversation_while_task_runs(runtime_fixture):
    runtime, agent_orch, _ = runtime_fixture

    # Setup slow multi-step plan
    planner = MagicMock()
    plan = Plan(
        user_goal="Lengthy data processing",
        steps=[
            PlanStep(step_id=1, capability_name="test.slow_work", description="Step 1"),
            PlanStep(step_id=2, capability_name="test.slow_work", description="Step 2"),
        ],
    )
    planner.create_plan.return_value = plan
    agent_orch.planner = planner
    agent_orch.can_handle = lambda q: "Lengthy data processing" in q

    # 1. Start Task A
    resp_a = runtime.dispatch("Lengthy data processing")
    assert resp_a.is_background is True
    task_a = resp_a.task
    assert task_a is not None
    assert task_a.is_active()

    # 2. Immediately submit Task B = "hello" while Task A is actively running
    resp_b = runtime.dispatch("hello")
    assert resp_b.is_background is False
    assert "Hello!" in resp_b.text or "help" in resp_b.text

    # 3. Task A must STILL be active and running independently
    assert task_a.is_active()
    assert task_a.status in (TaskStatus.RUNNING, TaskStatus.EXECUTING, TaskStatus.PLANNING)

    # 4. Cancel Task A
    cancel_resp = runtime.dispatch("cancel it")
    assert "Cancelled task" in cancel_resp.text
    assert task_a.status == TaskStatus.CANCELLED
