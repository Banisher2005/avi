"""Unit tests for agent context and events."""

import pytest
from avi.agent.context import OrchestrationContext, StepRecord, TaskStatus
from avi.agent.events import EventDispatcher, ProgressEvent, ProgressEventType
from avi.memory.models import Memory


def test_orchestration_context_initialization():
    ctx = OrchestrationContext(user_prompt="find my project files")
    assert ctx.user_prompt == "find my project files"
    assert ctx.status == TaskStatus.IDLE
    assert len(ctx.steps) == 0
    assert ctx.current_step() is None
    assert not ctx.is_terminal()


def test_orchestration_context_step_tracking():
    ctx = OrchestrationContext(user_prompt="open notes and search for meeting")
    s1 = StepRecord(step_index=0, capability_name="filesystem.search", args={"pattern": "meeting*"})
    s2 = StepRecord(step_index=1, capability_name="apps.open", args={"app": "notes"})
    ctx.steps = [s1, s2]

    assert ctx.current_step() == s1
    s1.status = "completed"
    s1.observation = ["meeting_notes.txt"]
    ctx.current_step_index = 1
    assert ctx.current_step() == s2
    assert len(ctx.completed_steps()) == 1

    d = ctx.to_dict()
    assert d["user_prompt"] == "open notes and search for meeting"
    assert len(d["steps"]) == 2
    assert d["steps"][0]["status"] == "completed"
    assert "meeting_notes.txt" in d["steps"][0]["observation"]


def test_event_dispatcher():
    dispatcher = EventDispatcher()
    received = []

    def callback(ev: ProgressEvent):
        received.append(ev)

    dispatcher.subscribe(callback)

    ev1 = ProgressEvent(
        event_type=ProgressEventType.TASK_STARTED,
        task_id="task-123",
        message="Starting task",
    )
    dispatcher.emit(ev1)

    assert len(received) == 1
    assert received[0].event_type == ProgressEventType.TASK_STARTED
    assert received[0].task_id == "task-123"

    # Test error isolation
    def broken_callback(ev: ProgressEvent):
        raise ValueError("Boom")

    dispatcher.subscribe(broken_callback)
    ev2 = ProgressEvent(
        event_type=ProgressEventType.STEP_COMPLETED,
        task_id="task-123",
        message="Step done",
    )
    dispatcher.emit(ev2)
    assert len(received) == 2

    # Unsubscribe
    dispatcher.unsubscribe(callback)
    dispatcher.emit(ev1)
    assert len(received) == 2
