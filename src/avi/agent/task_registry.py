"""Runtime Task Registry and resource lock manager for non-blocking concurrent agent tasks."""

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avi.agent.context import OrchestrationContext, TaskStatus
from avi.agent.events import ProgressEvent

logger = logging.getLogger("avi.agent.task_registry")


@dataclass
class RuntimeTask:
    """In-memory runtime tracking record for an active, paused, or completed task."""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    durable_task_id: str = ""
    goal: str = ""
    status: TaskStatus = TaskStatus.RUNNING
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    current_step_index: int = 0
    total_steps: int = 0
    current_step_desc: str = ""
    last_event: ProgressEvent | None = None
    cancellation_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)
    worker_thread: threading.Thread | None = None
    resources: set[str] = field(default_factory=set)
    is_write: bool = False
    context: OrchestrationContext | None = None
    result: Any | None = None
    error: str | None = None

    def is_active(self) -> bool:
        """Return True if task is currently executing, planning, or verifying."""
        return self.status in (
            TaskStatus.RUNNING,
            TaskStatus.EXECUTING,
            TaskStatus.PLANNING,
            TaskStatus.VERIFYING,
        )

    def is_paused(self) -> bool:
        """Return True if task is paused."""
        return self.status in (TaskStatus.PAUSED, TaskStatus.PAUSED_FOR_CONFIRMATION)

    def is_terminal(self) -> bool:
        """Return True if task has finished (completed, failed, or cancelled)."""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )

    def cancel(self) -> None:
        """Signal cancellation to the worker."""
        self.cancellation_event.set()
        self.status = TaskStatus.CANCELLED
        self.completed_at = time.time()
        if self.context:
            self.context.status = TaskStatus.CANCELLED

    def pause(self) -> None:
        """Signal pause to the worker."""
        self.pause_event.set()
        self.status = TaskStatus.PAUSED
        if self.context:
            self.context.status = TaskStatus.PAUSED

    def resume(self) -> None:
        """Resume paused task."""
        self.pause_event.clear()
        self.status = TaskStatus.RUNNING
        if self.context:
            self.context.status = TaskStatus.RUNNING

    def progress_str(self) -> str:
        """Return honest human-readable progress string."""
        if self.total_steps > 0:
            return f"{self.current_step_index}/{self.total_steps} steps"
        if self.current_step_desc:
            return self.current_step_desc
        if self.status == TaskStatus.PLANNING:
            return "Planning..."
        if self.status == TaskStatus.VERIFYING:
            return "Verifying..."
        return "In progress"

    def format_badge(self) -> str:
        """Return compact single-line task indicator."""
        prog = self.progress_str()
        short_goal = (self.goal[:35] + "…") if len(self.goal) > 35 else self.goal
        if self.status == TaskStatus.PAUSED:
            return f"⏸ {short_goal} · Paused"
        if self.status == TaskStatus.CANCELLED:
            return f"■ {short_goal} · Cancelled"
        if self.status == TaskStatus.COMPLETED:
            return f"✓ {short_goal} · Completed"
        if self.status == TaskStatus.FAILED:
            return f"✗ {short_goal} · Failed"
        return f"◉ {short_goal} · {prog}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize runtime task metadata."""
        return {
            "task_id": self.task_id,
            "durable_task_id": self.durable_task_id,
            "goal": self.goal,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_step": self.current_step_index,
            "total_steps": self.total_steps,
            "progress": self.progress_str(),
            "last_event": self.last_event.message if self.last_event else None,
            "error": self.error,
        }


class ResourceLockManager:
    """Lightweight resource/intent lock preventing conflicting concurrent mutations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._write_locks: dict[str, str] = {}  # resource -> task_id
        self._read_locks: dict[str, set[str]] = {}  # resource -> set of task_ids

    def acquire(self, task_id: str, resources: set[str], is_write: bool) -> bool:
        """Attempt to acquire locks for the specified resources."""
        with self._lock:
            if not resources:
                return True

            for res in resources:
                # Write requests conflict with any other write or read lock
                if is_write:
                    if res in self._write_locks and self._write_locks[res] != task_id:
                        return False
                    if res in self._read_locks and any(t != task_id for t in self._read_locks[res]):
                        return False
                else:
                    # Read requests conflict only with active write locks
                    if res in self._write_locks and self._write_locks[res] != task_id:
                        return False

            # Grant locks
            for res in resources:
                if is_write:
                    self._write_locks[res] = task_id
                else:
                    self._read_locks.setdefault(res, set()).add(task_id)
            return True

    def release(self, task_id: str) -> None:
        """Release all locks held by task_id."""
        with self._lock:
            # Clear writes
            for res in list(self._write_locks.keys()):
                if self._write_locks[res] == task_id:
                    del self._write_locks[res]

            # Clear reads
            for res in list(self._read_locks.keys()):
                self._read_locks[res].discard(task_id)
                if not self._read_locks[res]:
                    del self._read_locks[res]

    def is_locked(self, resource: str) -> bool:
        """Check if a specific resource is locked."""
        with self._lock:
            return resource in self._write_locks or bool(self._read_locks.get(resource))

    @staticmethod
    def extract_resources(prompt: str) -> tuple[set[str], bool]:
        """Extract coarse-grained resource keys and mutation intent from a prompt."""
        lower = prompt.lower()
        resources: set[str] = set()

        # Filesystem resources
        for folder in ("downloads", "documents", "desktop", "music", "pictures", "videos"):
            if folder in lower:
                resources.add(f"filesystem:{Path.home() / folder.capitalize()}")

        # Browser resources
        if any(
            term in lower
            for term in (
                "browse",
                "browser",
                "chrome",
                "firefox",
                "web",
                "search",
                "url",
                "http",
                ".com",
                ".org",
            )
        ):
            resources.add("browser:active_tab")

        # Application resources
        for app in ("terminal", "calc", "editor", "settings", "files"):
            if app in lower:
                resources.add(f"app:{app}")

        # Mutating vs read-only keywords
        write_keywords = (
            "organize",
            "move",
            "delete",
            "remove",
            "clean",
            "rename",
            "write",
            "create",
            "make",
            "download",
            "save",
            "install",
            "click",
            "type",
        )
        is_write = any(w in lower for w in write_keywords)

        return resources, is_write


class RuntimeTaskRegistry:
    """Thread-safe registry for managing active, paused, and recent background tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, RuntimeTask] = {}
        self._lock = threading.RLock()
        self.resources = ResourceLockManager()

    def register(self, task: RuntimeTask) -> RuntimeTask:
        """Register a new runtime task."""
        with self._lock:
            self._tasks[task.task_id] = task
            return task

    def get(self, task_id: str) -> RuntimeTask | None:
        """Retrieve task by exact ID or ID prefix."""
        with self._lock:
            if task_id in self._tasks:
                return self._tasks[task_id]
            for tid, t in self._tasks.items():
                if tid.startswith(task_id):
                    return t
            return None

    def list_tasks(self, status: TaskStatus | None = None) -> list[RuntimeTask]:
        """List all tracked tasks, optionally filtered by status, newest first."""
        with self._lock:
            tasks = list(self._tasks.values())
            if status is not None:
                tasks = [t for t in tasks if t.status == status]
            return sorted(tasks, key=lambda t: t.started_at, reverse=True)

    def active_tasks(self) -> list[RuntimeTask]:
        """List currently running, executing, or planning tasks."""
        with self._lock:
            return [t for t in self._tasks.values() if t.is_active()]

    def paused_tasks(self) -> list[RuntimeTask]:
        """List currently paused tasks."""
        with self._lock:
            return [t for t in self._tasks.values() if t.is_paused()]

    def recent_task(self) -> RuntimeTask | None:
        """Get the most recently active or started task."""
        with self._lock:
            active = self.active_tasks()
            if active:
                return active[0]
            paused = self.paused_tasks()
            if paused:
                return paused[0]
            all_tasks = self.list_tasks()
            return all_tasks[0] if all_tasks else None

    def resolve_task_reference(
        self, query: str
    ) -> tuple[RuntimeTask | None, bool, list[RuntimeTask]]:
        """Resolve natural references (pronouns, keywords, numbers) to a task.

        Returns:
            (resolved_task, is_ambiguous, candidate_tasks)
        """
        clean_q = query.strip().lower()
        with self._lock:
            active = self.active_tasks()
            paused = self.paused_tasks()
            candidates_pool = active or paused or self.list_tasks()

            if not candidates_pool:
                return None, False, []

            # 1. Match numeric task reference ("task 1", "task 2", "#1")
            num_match = re.search(r"(?:task\s+|#)(\d+)", clean_q)
            if num_match:
                idx = int(num_match.group(1)) - 1
                if 0 <= idx < len(candidates_pool):
                    return candidates_pool[idx], False, [candidates_pool[idx]]

            # 2. Match ID prefix if provided
            for t in candidates_pool:
                if t.task_id.lower().startswith(clean_q) or (
                    t.durable_task_id and t.durable_task_id.lower().startswith(clean_q)
                ):
                    return t, False, [t]

            # 3. Pronoun / anaphoric reference ("that", "it", "this", "the task")
            is_pronoun = any(
                re.search(rf"\b{p}\b", clean_q) for p in ("that", "it", "this", "the task", "task")
            )
            if is_pronoun:
                # If there's an active pool
                target_pool = active if active else paused
                if len(target_pool) == 1:
                    return target_pool[0], False, target_pool
                elif len(target_pool) > 1:
                    return None, True, target_pool

            # 4. Keyword matching against task goals
            # Extract content words from query (excluding commands like 'stop', 'pause', 'the')
            stop_words = {
                "stop",
                "pause",
                "resume",
                "cancel",
                "abort",
                "that",
                "it",
                "the",
                "task",
                "what",
                "is",
                "doing",
                "actually",
                "forget",
            }
            query_words = [w for w in re.findall(r"\b[a-zA-Z0-9_-]+\b", clean_q) if w not in stop_words]

            if query_words:
                def _find_matches(pool: list[RuntimeTask]) -> list[RuntimeTask]:
                    # First try: all query words present in task goal tokens
                    exact_matches = []
                    for t in pool:
                        t_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", t.goal.lower()))
                        if all(w in t_words for w in query_words):
                            exact_matches.append(t)
                    if exact_matches:
                        return exact_matches

                    # Second try: any query word present in task goal tokens
                    partial_matches = []
                    for t in pool:
                        t_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", t.goal.lower()))
                        if any(w in t_words for w in query_words):
                            partial_matches.append(t)
                    return partial_matches

                matches = _find_matches(candidates_pool)
                if not matches:
                    # If candidates_pool was active only, search all tasks
                    matches = _find_matches(self.list_tasks())

                if len(matches) == 1:
                    return matches[0], False, matches
                elif len(matches) > 1:
                    # Prefer active matching task if exactly one active
                    active_matches = [m for m in matches if m.is_active()]
                    if len(active_matches) == 1:
                        return active_matches[0], False, active_matches
                    return None, True, matches
                return None, False, []

            # 5. Default fallback for simple command like 'stop', 'pause' with no keywords
            target_pool = active if active else paused
            if len(target_pool) == 1:
                return target_pool[0], False, target_pool
            elif len(target_pool) > 1:
                return None, True, target_pool

            return None, False, []

    def format_tasks_table(self) -> str:
        """Render a clean CLI/UI representation of all active and recent tasks."""
        with self._lock:
            all_tasks = self.list_tasks()
            if not all_tasks:
                return "No active or recent tasks."

            lines = ["Active & Recent Tasks:"]
            for i, t in enumerate(all_tasks[:10], start=1):
                sym = {
                    TaskStatus.RUNNING: "◉",
                    TaskStatus.EXECUTING: "◉",
                    TaskStatus.PLANNING: "◌",
                    TaskStatus.VERIFYING: "◌",
                    TaskStatus.PAUSED: "⏸",
                    TaskStatus.PAUSED_FOR_CONFIRMATION: "◌",
                    TaskStatus.WAITING_CONFIRMATION: "◌",
                    TaskStatus.COMPLETED: "✓",
                    TaskStatus.FAILED: "✗",
                    TaskStatus.CANCELLED: "■",
                }.get(t.status, "•")

                goal_trunc = (t.goal[:26] + "…") if len(t.goal) > 26 else t.goal
                lines.append(f"  {sym} #{i:<2} {goal_trunc:<28} {t.progress_str():<16} [{t.status.value}]")

            return "\n".join(lines)
