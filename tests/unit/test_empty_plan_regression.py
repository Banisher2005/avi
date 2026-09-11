"""Regression test for empty plan handling.

Ensures empty or unplannable requests are cleanly marked FAILED and NEVER
report false success with 'Task completed with no executable plan.'
"""

from unittest.mock import MagicMock

import pytest

from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher, ProgressEventType
from avi.agent.models import Plan
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.capabilities.registry import CapabilityRegistry
from avi.memory.retriever import MemoryRetriever
from avi.storage.database import Database


@pytest.fixture
def empty_plan_orchestrator(tmp_path):
    db = Database(db_path=tmp_path / "test.db")
    retriever = MemoryRetriever(database=db)
    registry = MagicMock(spec=CapabilityRegistry)
    registry.get_model_catalog.return_value = []

    dispatcher = EventDispatcher()

    orch = AgentOrchestrator(
        registry=registry,
        database=db,
        memory_retriever=retriever,
        event_dispatcher=dispatcher,
    )
    planner = MagicMock(spec=AgentPlanner)
    # Return empty plan with 0 steps
    planner.create_plan.return_value = Plan(user_goal="impossible task", steps=[])
    orch.planner = planner
    return orch, dispatcher


def test_empty_plan_marked_failed_never_completed(empty_plan_orchestrator):
    orch, dispatcher = empty_plan_orchestrator
    emitted_events = []
    dispatcher.subscribe(lambda ev: emitted_events.append(ev))

    context = orch.run("clean the whole internet")

    # 1. Must be marked FAILED, NOT COMPLETED
    assert context.status == TaskStatus.FAILED
    assert context.status != TaskStatus.COMPLETED

    # 2. Must emit TASK_FAILED, NOT TASK_COMPLETED
    event_types = [e.event_type for e in emitted_events]
    assert ProgressEventType.TASK_FAILED in event_types
    assert ProgressEventType.TASK_COMPLETED not in event_types

    # 3. Final response must NOT be the buggy string
    assert "Task completed with no executable plan." not in context.final_response
    assert "Task completed" not in context.final_response
    assert context.final_response == "I couldn't find an executable plan for that request."


def test_none_plan_marked_failed_never_completed(tmp_path):
    db = Database(db_path=tmp_path / "test.db")
    retriever = MemoryRetriever(database=db)
    registry = MagicMock(spec=CapabilityRegistry)
    registry.get_model_catalog.return_value = []

    dispatcher = EventDispatcher()
    orch = AgentOrchestrator(
        registry=registry,
        database=db,
        memory_retriever=retriever,
        event_dispatcher=dispatcher,
    )
    planner = MagicMock(spec=AgentPlanner)
    planner.create_plan.return_value = None
    orch.planner = planner

    context = orch.run("something impossible")
    assert context.status == TaskStatus.FAILED
    assert context.status != TaskStatus.COMPLETED
    assert "Task completed with no executable plan." not in context.final_response
    assert context.final_response == "I couldn't find an executable plan for that request."
