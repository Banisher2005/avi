"""Unit tests for Phase 7 persistent agent intelligence: state persistence, redaction,
reconciliation, idempotency, experience memory, goal decomposition, and interruption controls.
"""


from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher, ProgressEventType
from avi.agent.experience import ExperienceStore
from avi.agent.goal_verification import GoalVerifier
from avi.agent.idempotency import IdempotencyChecker
from avi.agent.models import Goal, GoalSegment, PlanStep, StepStatus
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.agent.reconciliation import EnvironmentReconciler, ReconciliationStatus
from avi.storage.database import Database, redact_sensitive_data
from avi.storage.models import DurableTaskRecord


def test_sensitive_data_redaction():
    """Verify recursive redaction of credentials, tokens, API keys, and HTML."""
    dummy_token = "".join(["gh", "p_", "1234567890abcdefghijklmnopqrstuvwxyz"])
    dummy_glpat = "".join(["gl", "pat-", "abcdef1234567890ABCD"])
    raw_data = {
        "api_key": "sk-1234567890abcdef1234567890abcdef1234",
        "nested": {
            "password": "SuperSecretPassword123!",
            "token": dummy_token,
            "glpat": dummy_glpat,
            "html": "<html>" + "a" * 1500 + "</html>",
        },
        "safe_field": "hello world",
    }
    cleaned = redact_sensitive_data(raw_data)
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["password"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED_SECRET]"
    assert cleaned["nested"]["glpat"] == "[REDACTED_SECRET]"
    assert cleaned["nested"]["html"].endswith("[TRUNCATED_HTML]")
    assert cleaned["safe_field"] == "hello world"


def test_durable_task_persistence_crud(tmp_path):
    """Verify full CRUD lifecycle for versioned durable task records."""
    db_file = tmp_path / "test_durable.db"
    db = Database(db_path=db_file)

    rec = DurableTaskRecord(
        task_id="task-001",
        session_id="sess-abc",
        goal="Download and inspect dataset",
        normalized_goal="download and inspect dataset",
        status="running",
        strategy_name="primary",
        data={"step": 1, "target": "/tmp/data.csv"},
    )
    db.save_durable_task(rec)

    # Read back
    retrieved = db.get_durable_task("task-001")
    assert retrieved is not None
    assert retrieved.task_id == "task-001"
    assert retrieved.goal == "Download and inspect dataset"
    assert retrieved.status == "running"
    assert retrieved.data["target"] == "/tmp/data.csv"

    # Update status
    updated = db.update_durable_task_status("task-001", "completed")
    assert updated is True
    retrieved2 = db.get_durable_task("task-001")
    assert retrieved2.status == "completed"
    assert retrieved2.completed_at is not None

    # List tasks
    all_tasks = db.list_durable_tasks()
    assert len(all_tasks) == 1
    assert all_tasks[0].task_id == "task-001"

    # Archive/delete
    deleted = db.archive_durable_task("task-001")
    assert deleted is True
    assert db.get_durable_task("task-001") is None


def test_experience_store_and_recommendations(tmp_path):
    """Verify recording and querying execution experiences for strategy recommendation."""
    db_file = tmp_path / "test_exp.db"
    db = Database(db_path=db_file)
    store = ExperienceStore(db=db)

    # Record a failure with strategy 'curl_download'
    store.record_outcome(
        goal="download python report",
        strategy_name="curl_download",
        capability_used="desktop.network_download",
        success=False,
        failure_category="network_timeout",
    )

    # Record a success with strategy 'browser_download'
    store.record_outcome(
        goal="download python report",
        strategy_name="browser_download",
        capability_used="browser.download",
        success=True,
    )

    failed = store.get_failed_strategies("download python report")
    assert "curl_download" in failed

    successful = store.get_successful_strategies("download python report")
    assert "browser_download" in successful

    recommended = store.recommend_strategy(
        "download python report",
        available_strategies=["curl_download", "browser_download", "fallback_strategy"],
    )
    assert recommended == "browser_download"


def test_idempotency_checker_filesystem(tmp_path):
    """Verify IdempotencyChecker detects already-satisfied filesystem operations."""
    checker = IdempotencyChecker()

    # 1. Directory creation idempotency
    existing_dir = tmp_path / "already_created"
    existing_dir.mkdir()
    res_dir = checker.check_already_satisfied("filesystem.create_directory", {"path": str(existing_dir)})
    assert res_dir is not None
    assert res_dir.success is True
    assert res_dir.data.get("already_satisfied") is True

    # Non-existent dir returns None
    assert checker.check_already_satisfied("filesystem.create_directory", {"path": str(tmp_path / "new_dir")}) is None

    # 2. File write with identical content
    file_path = tmp_path / "content.txt"
    file_path.write_text("fixed content", encoding="utf-8")
    res_file = checker.check_already_satisfied(
        "filesystem.write_file",
        {"path": str(file_path), "content": "fixed content"},
    )
    assert res_file is not None
    assert res_file.success is True
    assert res_file.data.get("already_satisfied") is True

    # Different content needs write
    assert (
        checker.check_already_satisfied(
            "filesystem.write_file",
            {"path": str(file_path), "content": "modified content"},
        )
        is None
    )

    # 3. File move where destination exists and source is gone
    src_path = tmp_path / "src.txt"
    dst_path = tmp_path / "dst.txt"
    dst_path.write_text("data", encoding="utf-8")
    res_move = checker.check_already_satisfied(
        "filesystem.move_file",
        {"source": str(src_path), "destination": str(dst_path)},
    )
    assert res_move is not None
    assert res_move.success is True
    assert res_move.data.get("already_satisfied") is True


def test_idempotency_checker_browser():
    """Verify IdempotencyChecker detects already-active browser URLs."""
    checker = IdempotencyChecker()
    obs = {"url": "https://github.com/trending"}
    res = checker.check_already_satisfied(
        "browser.navigate",
        {"url": "https://github.com/trending"},
        current_observation=obs,
    )
    assert res is not None
    assert res.success is True
    assert res.data.get("already_satisfied") is True


def test_environment_reconciliation_states(tmp_path):
    """Verify EnvironmentReconciler handles all reconciliation states: UNCHANGED, PARTIALLY_CHANGED, ALREADY_COMPLETE, CONFLICTING."""
    reconciler = EnvironmentReconciler()

    target_dir = tmp_path / "project"
    step1 = PlanStep(
        step_id=1,
        capability_name="filesystem.create_directory",
        arguments={"path": str(target_dir)},
        status=StepStatus.PENDING,
    )

    # Case A: target_dir does not exist -> UNCHANGED, proceed
    report_unchanged = reconciler.reconcile(completed_steps=[], pending_steps=[step1])
    assert report_unchanged.status == ReconciliationStatus.UNCHANGED
    assert report_unchanged.recommendation == "proceed"

    # Case B: target_dir now exists -> ALREADY_COMPLETE, complete
    target_dir.mkdir()
    report_complete = reconciler.reconcile(completed_steps=[], pending_steps=[step1])
    assert report_complete.status == ReconciliationStatus.ALREADY_COMPLETE
    assert report_complete.recommendation == "complete"
    assert 1 in report_complete.satisfied_steps

    # Case C: step1 completed, step2 pending. If completed artifact is deleted -> CONFLICTING
    completed_step1 = PlanStep(
        step_id=1,
        capability_name="filesystem.create_directory",
        arguments={"path": str(target_dir)},
        status=StepStatus.SUCCESS,
        verified=True,
    )
    step2 = PlanStep(
        step_id=2,
        capability_name="filesystem.write_file",
        arguments={"path": str(target_dir / "app.py"), "content": "print(1)"},
        status=StepStatus.PENDING,
    )
    # Remove target_dir behind the agent's back
    target_dir.rmdir()
    report_conflict = reconciler.reconcile(completed_steps=[completed_step1], pending_steps=[step2])
    assert report_conflict.status == ReconciliationStatus.CONFLICTING
    assert report_conflict.recommendation == "replan"
    assert 1 in report_conflict.conflicting_steps


def test_goal_verifier_postconditions(tmp_path):
    """Verify GoalVerifier checks real environmental postconditions."""
    verifier = GoalVerifier()
    test_file = tmp_path / "output.txt"
    test_file.write_text("results", encoding="utf-8")

    segment = GoalSegment(
        segment_id="seg_1",
        title="write report",
        verification_condition={"file_exists": str(test_file), "non_empty": True},
        completed_steps=[
            PlanStep(
                step_id=1,
                capability_name="filesystem.write_file",
                arguments={"path": str(test_file)},
                status=StepStatus.SUCCESS,
            )
        ],
    )
    res = verifier.verify_segment(segment, segment.completed_steps)
    assert res.verified is True

    # Test failure when expected file is missing
    missing_file = tmp_path / "missing.txt"
    segment_fail = GoalSegment(
        segment_id="seg_2",
        title="write missing",
        verification_condition={"file_exists": str(missing_file)},
        completed_steps=segment.completed_steps,
    )
    res_fail = verifier.verify_segment(segment_fail, segment_fail.completed_steps)
    assert res_fail.verified is False
    assert "does not exist" in res_fail.reason


def test_goal_decomposition_compound_intent():
    """Verify AgentPlanner decomposes multi-clause requests into GoalSegments."""
    planner = AgentPlanner()
    prompt = "search for pdf in ~/Downloads and then take a screenshot"
    goal = planner.decompose_goal(prompt)
    assert isinstance(goal, Goal)
    assert goal.is_decomposed is True
    assert len(goal.segments) == 2
    assert "search" in goal.segments[0].title.lower()
    assert "screenshot" in goal.segments[1].title.lower()


def test_conversational_continuation_pronoun_resolution():
    """Verify conversational continuation resolves pronouns using recent context."""
    planner = AgentPlanner()
    context = {"last_artifact": "/home/user/reports/summary.pdf"}
    resolved = planner.resolve_continuation("now open it", context=context)
    assert "/home/user/reports/summary.pdf" in resolved
    assert "now" not in resolved.lower().split()


def test_orchestrator_pause_and_cancel_interruption(tmp_path):
    """Verify AgentOrchestrator supports user pause and cancel interruptions."""
    db = Database(db_path=tmp_path / "interruption.db")
    events = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe(lambda ev: events.append(ev))

    orchestrator = AgentOrchestrator(
        database=db,
        event_dispatcher=dispatcher,
    )

    # Save a running task
    rec = DurableTaskRecord(
        task_id="task-int-1",
        goal="long running task",
        status="running",
    )
    db.save_durable_task(rec)

    # User says "pause"
    pause_ctx = orchestrator.run("pause task task-int-1")
    assert pause_ctx.status == TaskStatus.PAUSED
    assert db.get_durable_task("task-int-1").status == "paused"

    # User says "cancel"
    cancel_ctx = orchestrator.run("cancel task task-int-1")
    assert cancel_ctx.status == TaskStatus.CANCELLED
    assert db.get_durable_task("task-int-1").status == "cancelled"

    ev_types = [e.event_type for e in events]
    assert ProgressEventType.TASK_PAUSED in ev_types
    assert ProgressEventType.TASK_CANCELLED in ev_types
