"""Unit tests for agent context and events."""

from avi.agent.context import OrchestrationContext, StepRecord, TaskStatus
from avi.agent.events import EventDispatcher, ProgressEvent, ProgressEventType


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


def test_step_record_structured_fields():
    step = StepRecord(
        step_index=0,
        capability_name="desktop.screenshot",
        args={"format": "png"},
        description="Take screenshot",
        input_data={"format": "png"},
        output_data={"path": "/home/user/shot.png"},
        observation={"path": "/home/user/shot.png"},
        verification_result=True,
        verification_message="Verified file exists",
        duration_ms=45.2,
        retry_count=1,
        artifact_path="/home/user/shot.png",
        artifacts=["/home/user/shot.png"],
    )

    d = step.to_dict()
    assert d["step_index"] == 0
    assert d["capability_name"] == "desktop.screenshot"
    assert d["input_data"] == {"format": "png"}
    assert d["output_data"] == {"path": "/home/user/shot.png"}
    assert d["verification_result"] is True
    assert d["verification_message"] == "Verified file exists"
    assert d["duration_ms"] == 45.2
    assert d["retry_count"] == 1
    assert d["artifact_path"] == "/home/user/shot.png"
    assert d["artifacts"] == ["/home/user/shot.png"]


def test_agent_executor_verification_contracts(tmp_path):
    from unittest.mock import MagicMock

    from avi.agent.executor import AgentExecutor
    from avi.agent.models import PlanStep
    from avi.capabilities.models import CapabilityResult, ExecutionStatus
    from avi.capabilities.registry import CapabilityRegistry

    executor = AgentExecutor(registry=MagicMock(spec=CapabilityRegistry))

    # 1. Directory creation
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    step_mkdir = PlanStep(step_id=1, capability_name="filesystem.create_directory", arguments={"path": str(test_dir)})
    res_mkdir = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS, data={"path": str(test_dir)})
    assert executor._verify_step(step_mkdir, res_mkdir) is True

    # 2. File copy
    src_file = tmp_path / "src.txt"
    src_file.write_text("content")
    dst_file = tmp_path / "dst.txt"
    dst_file.write_text("content")
    step_cp = PlanStep(step_id=2, capability_name="filesystem.copy", arguments={"source": str(src_file), "destination": str(dst_file)})
    res_cp = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS, data={"destination": str(dst_file)})
    assert executor._verify_step(step_cp, res_cp) is True

    # 3. File move
    dst_moved = tmp_path / "moved.txt"
    dst_moved.write_text("content")
    step_mv = PlanStep(step_id=3, capability_name="filesystem.move", arguments={"source": str(src_file), "destination": str(dst_moved)})
    # If source still exists, move verification fails
    res_mv = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS, data={"destination": str(dst_moved)})
    assert executor._verify_step(step_mv, res_mv) is False
    # Once source is removed, move verification passes
    src_file.unlink()
    assert executor._verify_step(step_mv, res_mv) is True

    # 4. File delete
    del_file = tmp_path / "delete_me.txt"
    step_del = PlanStep(step_id=4, capability_name="filesystem.delete", arguments={"path": str(del_file)})
    res_del = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS)
    # File does not exist -> verified True
    assert executor._verify_step(step_del, res_del) is True
    # If file still exists -> verified False
    del_file.write_text("still here")
    assert executor._verify_step(step_del, res_del) is False

    # 5. Search
    step_srch = PlanStep(step_id=5, capability_name="filesystem.search")
    res_srch_ok = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS, data={"matches": ["f1.txt"]})
    assert executor._verify_step(step_srch, res_srch_ok) is True
    res_srch_bad = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS, data={})
    assert executor._verify_step(step_srch, res_srch_bad) is False

    # 6. Clipboard
    step_clip = PlanStep(step_id=6, capability_name="desktop.clipboard.get")
    res_clip = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS, data={"text": "abc"})
    assert executor._verify_step(step_clip, res_clip) is True

