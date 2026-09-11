"""Unit tests for Phase 8 RuntimeTask, ResourceLockManager, and RuntimeTaskRegistry."""

from avi.agent.context import TaskStatus
from avi.agent.task_registry import ResourceLockManager, RuntimeTask, RuntimeTaskRegistry


def test_runtime_task_lifecycle_and_badge():
    task = RuntimeTask(
        task_id="task-1234",
        goal="Downloading python docs",
        total_steps=5,
        current_step_index=2,
    )
    assert task.task_id == "task-1234"
    assert task.status == TaskStatus.RUNNING
    assert task.is_active() is True
    assert not task.is_terminal()
    assert task.progress_str() == "2/5 steps"

    # Badge for running
    badge = task.format_badge()
    assert "◉" in badge
    assert "Downloading python docs" in badge

    # Pause
    task.pause()
    assert task.is_paused() is True
    assert task.status == TaskStatus.PAUSED
    badge_paused = task.format_badge()
    assert "⏸" in badge_paused

    # Resume
    task.resume()
    assert task.is_active() is True
    assert task.status == TaskStatus.RUNNING

    # Cancel
    task.cancel()
    assert task.is_terminal() is True
    assert task.status == TaskStatus.CANCELLED
    badge_cancelled = task.format_badge()
    assert "■" in badge_cancelled

    # Completed
    task.status = TaskStatus.COMPLETED
    assert task.is_terminal() is True
    badge_completed = task.format_badge()
    assert "✓" in badge_completed

    # Failed
    task.status = TaskStatus.FAILED
    assert task.is_terminal() is True
    badge_failed = task.format_badge()
    assert "✗" in badge_failed


def test_resource_lock_manager():
    lock_mgr = ResourceLockManager()

    # Task 1 acquires write lock
    ok = lock_mgr.acquire("task-1", {"filesystem:/home/user/doc.txt", "browser:tab1"}, is_write=True)
    assert ok is True
    assert lock_mgr.is_locked("filesystem:/home/user/doc.txt")
    assert lock_mgr.is_locked("browser:tab1")

    # Task 2 tries to acquire write lock on overlapping resource -> fails
    ok2 = lock_mgr.acquire("task-2", {"browser:tab1", "app:terminal"}, is_write=True)
    assert ok2 is False
    assert not lock_mgr.is_locked("app:terminal")

    # Task 2 tries to acquire read lock on write-locked resource -> fails
    ok2_read = lock_mgr.acquire("task-2", {"browser:tab1"}, is_write=False)
    assert ok2_read is False

    # Task 3 acquires disjoint resource -> succeeds
    ok3 = lock_mgr.acquire("task-3", {"filesystem:/tmp/data.csv"}, is_write=True)
    assert ok3 is True
    assert lock_mgr.is_locked("filesystem:/tmp/data.csv")

    # Release task 1
    lock_mgr.release("task-1")
    assert not lock_mgr.is_locked("browser:tab1")
    assert not lock_mgr.is_locked("filesystem:/home/user/doc.txt")

    # Now task 2 can acquire
    ok4 = lock_mgr.acquire("task-2", {"browser:tab1", "app:terminal"}, is_write=True)
    assert ok4 is True
    assert lock_mgr.is_locked("browser:tab1")
    assert lock_mgr.is_locked("app:terminal")


def test_resource_lock_manager_read_locks():
    lock_mgr = ResourceLockManager()

    # Concurrent read locks on same resource are permitted
    assert lock_mgr.acquire("task-1", {"filesystem:/data.txt"}, is_write=False) is True
    assert lock_mgr.acquire("task-2", {"filesystem:/data.txt"}, is_write=False) is True

    # But write lock fails
    assert lock_mgr.acquire("task-3", {"filesystem:/data.txt"}, is_write=True) is False

    # Once reads are released, write succeeds
    lock_mgr.release("task-1")
    lock_mgr.release("task-2")
    assert lock_mgr.acquire("task-3", {"filesystem:/data.txt"}, is_write=True) is True


def test_resource_lock_extract_resources():
    res, is_write = ResourceLockManager.extract_resources("please organize my downloads folder")
    assert any("Downloads" in r for r in res)
    assert is_write is True

    res2, is_write2 = ResourceLockManager.extract_resources("browse to google.com and check news")
    assert "browser:active_tab" in res2
    assert is_write2 is False


def test_runtime_task_registry_crud_and_status():
    registry = RuntimeTaskRegistry()

    t1 = RuntimeTask(task_id="task-1", goal="Run test suite")
    registry.register(t1)

    assert registry.get("task-1") == t1
    assert registry.get("task-") == t1  # prefix search
    assert len(registry.active_tasks()) == 1

    # Mark completed
    t1.status = TaskStatus.COMPLETED
    assert len(registry.active_tasks()) == 0
    assert len(registry.list_tasks(status=TaskStatus.COMPLETED)) == 1


def test_runtime_task_registry_reference_resolution():
    registry = RuntimeTaskRegistry()

    t1 = RuntimeTask(task_id="t1", goal="Download dataset", started_at=100.0)
    registry.register(t1)
    t2 = RuntimeTask(task_id="t2", goal="Analyze audio logs", started_at=200.0)
    registry.register(t2)

    # 1. Exact ID or prefix
    task, ambig, _ = registry.resolve_task_reference("t1")
    assert task == t1
    assert ambig is False

    # 2. Pronoun / anaphoric resolution ("that", "it") -> returns single active task if one, or ambig if multiple
    # Currently both are active
    task, ambig, pool = registry.resolve_task_reference("that")
    assert ambig is True  # multiple active tasks
    assert len(pool) == 2

    # Complete t1 -> only t2 active
    t1.status = TaskStatus.COMPLETED
    task, ambig, _ = registry.resolve_task_reference("that")
    assert task == t2
    assert ambig is False

    task, ambig, _ = registry.resolve_task_reference("it")
    assert task == t2
    assert ambig is False

    # 3. Keyword matching
    task, ambig, _ = registry.resolve_task_reference("download")
    assert task == t1

    task, ambig, _ = registry.resolve_task_reference("audio")
    assert task == t2

    # 4. Numeric matching (#1, task 1)
    task, ambig, _ = registry.resolve_task_reference("#1")
    assert task is not None


def test_runtime_task_registry_ambiguity():
    registry = RuntimeTaskRegistry()
    t1 = RuntimeTask(task_id="t1", goal="Download dataset A", started_at=100.0)
    t2 = RuntimeTask(task_id="t2", goal="Download dataset B", started_at=200.0)
    registry.register(t1)
    registry.register(t2)

    # Keyword 'download' matches both active tasks -> ambiguous
    task, ambig, candidates = registry.resolve_task_reference("download")
    assert task is None
    assert ambig is True
    assert len(candidates) == 2

    # Specific keyword matches unique task
    task_a, ambig_a, _ = registry.resolve_task_reference("dataset a")
    assert task_a == t1
    assert ambig_a is False


def test_format_tasks_table():
    registry = RuntimeTaskRegistry()
    empty_table = registry.format_tasks_table()
    assert "No active or recent tasks." in empty_table

    t1 = RuntimeTask(task_id="t-1", goal="Build project")
    registry.register(t1)
    table = registry.format_tasks_table()
    assert "Active & Recent Tasks:" in table
    assert "#1" in table
    assert "Build project" in table
