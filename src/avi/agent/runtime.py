"""Interactive Agent Runtime coordinating non-blocking execution, task registry, and event streaming."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from avi.agent.context import TaskStatus
from avi.agent.events import EventDispatcher, ProgressEvent, ProgressEventType
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.task_registry import ResourceLockManager, RuntimeTask, RuntimeTaskRegistry
from avi.config import Config
from avi.core.router import Router

logger = logging.getLogger("avi.agent.runtime")


@dataclass
class RuntimeResponse:
    """Unified response from runtime input dispatch."""

    text: str
    is_background: bool = False
    task_id: str | None = None
    task: RuntimeTask | None = None
    requires_confirmation: bool = False
    proposal: Any | None = None
    is_blocked: bool = False
    clarification_needed: bool = False
    candidates: list[RuntimeTask] = field(default_factory=list)


class AgentRuntime:
    """Non-blocking interactive runtime managing background agent execution, fast paths, and controls."""

    def __init__(
        self,
        config: Config | None = None,
        router: Router | None = None,
        assistant_orchestrator: Any | None = None,
        agent_orchestrator: AgentOrchestrator | None = None,
        task_registry: RuntimeTaskRegistry | None = None,
        events: EventDispatcher | None = None,
    ) -> None:
        self.config = config or Config.load()
        self.router = router or Router(self.config)
        if assistant_orchestrator is None:
            from avi.orchestrator.orchestrator import AssistantOrchestrator

            self.assistant_orchestrator = AssistantOrchestrator(
                config=self.config, router=self.router
            )
        else:
            self.assistant_orchestrator = assistant_orchestrator
        self.agent_orchestrator = (
            agent_orchestrator
            or getattr(self.assistant_orchestrator, "agent_orchestrator", None)
            or AgentOrchestrator()
        )
        self.task_registry = task_registry or RuntimeTaskRegistry()
        self.events = events or getattr(self.agent_orchestrator, "events", None) or EventDispatcher()

        # Connect internal progress listener to update runtime tasks
        if hasattr(self.events, "subscribe"):
            self.events.subscribe(self._sync_task_state_from_event)

    def _sync_task_state_from_event(self, event: ProgressEvent) -> None:
        """Keep RuntimeTask in task_registry synchronized with real-time lifecycle events."""
        task = self.task_registry.get(event.task_id)
        if not task:
            return

        task.last_event = event
        if event.step_index:
            task.current_step_index = event.step_index
        if "steps_count" in event.data:
            task.total_steps = event.data["steps_count"]
        if event.event_type == ProgressEventType.STEP_STARTED:
            task.current_step_desc = event.message
        elif event.event_type in (ProgressEventType.TASK_COMPLETED, ProgressEventType.GOAL_COMPLETED):
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
        elif event.event_type in (ProgressEventType.TASK_FAILED, ProgressEventType.GOAL_FAILED):
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
        elif event.event_type == ProgressEventType.TASK_CANCELLED:
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
        elif event.event_type == ProgressEventType.TASK_PAUSED:
            task.status = TaskStatus.PAUSED

    def dispatch(
        self,
        query: str,
        session_id: str = "",
        confirmed: bool = False,
        context: Any | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> RuntimeResponse:
        """Dispatch user input without blocking the main event loop for long-running tasks."""
        clean_q = query.strip()
        if not clean_q:
            return RuntimeResponse(text="")

        # ── Step 1: Runtime Management Commands (tasks, status, pause, resume, cancel) ──
        cmd_res = self._handle_runtime_command(clean_q)
        if cmd_res is not None:
            return cmd_res

        # ── Step 2: Deterministic Fast-Path Check (< 2ms) ───────────────────
        fast_result = self.router.check_fast_path(clean_q)
        if isinstance(fast_result, str):
            return RuntimeResponse(text=fast_result.strip(), is_background=False)

        # ── Step 3: Immediate / Read-Only Assistant Intents ──────────────────
        from avi.assistant.intents import AssistantIntentType, detect_assistant_intent

        last_turn = (
            self.assistant_orchestrator.history.last_turn
            if hasattr(self.assistant_orchestrator, "history")
            else None
        )
        intent = detect_assistant_intent(clean_q, last_turn=last_turn)

        # Immediate fast intents that don't need background agent planning (excluding greeting/small-talk)
        immediate_intents = (
            AssistantIntentType.VOLUME_GET,
            AssistantIntentType.VOLUME_SET,
            AssistantIntentType.MEDIA_CONTROL,
            AssistantIntentType.SYSTEM_INFO,
            AssistantIntentType.OPEN_APP,
            AssistantIntentType.OPEN_URL,
            AssistantIntentType.OPEN_DIR,
            AssistantIntentType.SCREENSHOT,
            AssistantIntentType.TIMER,
        )

        if intent.intent_type in immediate_intents:
            res = self.assistant_orchestrator.handle(clean_q, auto_execute_actions=True)
            if res.is_blocked:
                return RuntimeResponse(
                    text=res.text or "Action blocked by safety policy.",
                    is_blocked=True,
                )
            if res.requires_confirmation:
                proposal = res.command_request or res.plan
                return RuntimeResponse(
                    text=res.text,
                    requires_confirmation=True,
                    proposal=proposal,
                )
            return RuntimeResponse(text=res.text.strip() if res.text else "", is_background=False)

        # ── Step 4: Complex Multi-step Agent Tasks (Non-blocking Background Worker) ─
        if self.agent_orchestrator.can_handle(clean_q):
            return self._dispatch_background_task(clean_q, session_id=session_id, confirmed=confirmed)

        # ── Step 5: Conversational LLM Synthesis Fallback ───────────────────
        if self.config.stream:
            buffered = ""
            stream_iter = iter(self.router.route(clean_q, context=context, stream=True))
            is_proposal = False

            for chunk in stream_iter:
                buffered += chunk
                clean_buf = buffered.strip().upper()
                if any(
                    clean_buf.startswith(p)
                    for p in ("COMMAND:", "PROPOSAL:", "```JSON", '{"', "{")
                ):
                    is_proposal = True
                    break
                if len(buffered.strip()) >= 12:
                    break

            if is_proposal:
                full_text = buffered + "".join(stream_iter)
                proposal = self.router.parse_command_proposal(full_text)
                return RuntimeResponse(
                    text=full_text,
                    requires_confirmation=(proposal is not None),
                    proposal=proposal,
                )
            else:
                remaining_chunks = list(stream_iter)
                full_text = buffered + "".join(remaining_chunks)
                return RuntimeResponse(text=full_text.rstrip(), is_background=False)
        else:
            resp = self.router.route_full(clean_q, context=context)
            proposal = self.router.parse_command_proposal(resp.text)
            return RuntimeResponse(
                text=resp.text.strip(),
                requires_confirmation=(proposal is not None),
                proposal=proposal,
            )

    def _handle_runtime_command(self, query: str) -> RuntimeResponse | None:
        """Evaluate local runtime management commands without calling an LLM."""
        lower = query.lower()

        # 1. Tasks list / status table
        if lower in ("tasks", "task list", "list tasks", "status", "show tasks"):
            table = self.task_registry.format_tasks_table()
            return RuntimeResponse(text=table, is_background=False)

        # 2. What are you doing?
        if any(
            p in lower
            for p in (
                "what are you doing",
                "what're you doing",
                "what is avi doing",
                "what is the task doing",
                "what is task doing",
                "current task",
            )
        ):
            active = self.task_registry.active_tasks()
            if not active:
                return RuntimeResponse(
                    text="I'm not currently running any background tasks. Ready for your next request.",
                    is_background=False,
                )
            if len(active) == 1:
                t = active[0]
                return RuntimeResponse(
                    text=f"I'm currently working on '{t.goal}'. Progress: {t.progress_str()}.",
                    is_background=False,
                )
            # Multiple active tasks
            task_summaries = [f"'{t.goal}' ({t.progress_str()})" for t in active]
            return RuntimeResponse(
                text=f"I'm currently running {len(active)} tasks: {', '.join(task_summaries)}.",
                is_background=False,
            )

        # 3. Stop / Cancel commands
        if any(lower.startswith(p) for p in ("stop", "cancel", "abort", "forget that", "never mind")):
            task, is_ambiguous, candidates = self.task_registry.resolve_task_reference(query)
            if is_ambiguous:
                opts = ", ".join(f"'{c.goal}'" for c in candidates)
                return RuntimeResponse(
                    text=f"Multiple tasks are running ({opts}). Which task would you like to stop?",
                    clarification_needed=True,
                    candidates=candidates,
                )
            if task:
                self.agent_orchestrator.cancel_task(task.task_id)
                task.cancel()
                self.task_registry.resources.release(task.task_id)
                return RuntimeResponse(
                    text=f"✓ Cancelled task: {task.goal}.",
                    is_background=False,
                    task_id=task.task_id,
                )
            return RuntimeResponse(text="No active task to stop.", is_background=False)

        # 4. Pause commands
        if any(lower.startswith(p) for p in ("pause", "hold on", "wait on")):
            task, is_ambiguous, candidates = self.task_registry.resolve_task_reference(query)
            if is_ambiguous:
                opts = ", ".join(f"'{c.goal}'" for c in candidates)
                return RuntimeResponse(
                    text=f"Multiple tasks are running ({opts}). Which task would you like to pause?",
                    clarification_needed=True,
                    candidates=candidates,
                )
            if task:
                self.agent_orchestrator.pause_task(task.task_id)
                task.pause()
                return RuntimeResponse(
                    text=f"⏸ Paused task: {task.goal}.",
                    is_background=False,
                    task_id=task.task_id,
                )
            return RuntimeResponse(text="No active task to pause.", is_background=False)

        # 5. Resume commands
        if any(lower.startswith(p) for p in ("resume", "continue")):
            task, is_ambiguous, candidates = self.task_registry.resolve_task_reference(query)
            if is_ambiguous:
                opts = ", ".join(f"'{c.goal}'" for c in candidates)
                return RuntimeResponse(
                    text=f"Multiple paused tasks found ({opts}). Which task would you like to resume?",
                    clarification_needed=True,
                    candidates=candidates,
                )
            if task:
                return self._resume_background_task(task)
            return RuntimeResponse(text="No paused task to resume.", is_background=False)

        return None

    def _dispatch_background_task(
        self, prompt: str, session_id: str = "", confirmed: bool = False
    ) -> RuntimeResponse:
        """Spawn a non-blocking background worker thread for a complex agent task."""
        resources, is_write = ResourceLockManager.extract_resources(prompt)
        task = RuntimeTask(
            goal=prompt,
            is_write=is_write,
            resources=resources,
            status=TaskStatus.PLANNING,
        )

        # Resource collision check
        can_acquire = self.task_registry.resources.acquire(task.task_id, resources, is_write)
        if not can_acquire:
            conflict_res = list(resources)[0] if resources else "system resources"
            return RuntimeResponse(
                text=f"Task '{prompt}' cannot start yet because another task is actively modifying {conflict_res}. Please wait or pause that task.",
                is_background=False,
            )

        self.task_registry.register(task)

        def _worker() -> None:
            try:
                task.status = TaskStatus.RUNNING
                ctx = self.agent_orchestrator.run(
                    prompt,
                    confirmed=confirmed,
                    session_id=session_id,
                    cancellation_token=task.cancellation_event,
                    pause_token=task.pause_event,
                )
                task.context = ctx
                task.status = ctx.status
                task.result = ctx.final_response
                if ctx.status == TaskStatus.COMPLETED:
                    task.completed_at = time.time()
                elif ctx.status == TaskStatus.FAILED:
                    task.error = ctx.error_details[-1] if ctx.error_details else "Task failed."
                    task.completed_at = time.time()
                elif ctx.status == TaskStatus.CANCELLED:
                    task.completed_at = time.time()
            except Exception as exc:
                logger.exception("Error in background worker for task %s: %s", task.task_id, exc)
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.completed_at = time.time()
            finally:
                self.task_registry.resources.release(task.task_id)

        thread = threading.Thread(
            target=_worker,
            daemon=True,
            name=f"avi-task-{task.task_id[:8]}",
        )
        task.worker_thread = thread
        thread.start()

        ack_msg = f"Started task: '{prompt}' in background."
        return RuntimeResponse(
            text=ack_msg,
            is_background=True,
            task_id=task.task_id,
            task=task,
        )

    def _resume_background_task(self, task: RuntimeTask) -> RuntimeResponse:
        """Resume a paused task in a background worker."""
        task.resume()

        def _worker() -> None:
            try:
                task.status = TaskStatus.RUNNING
                ctx = self.agent_orchestrator.resume_task(
                    task.task_id,
                )
                task.context = ctx
                task.status = ctx.status
                task.result = ctx.final_response
                if ctx.status == TaskStatus.COMPLETED:
                    task.completed_at = time.time()
            except Exception as exc:
                logger.exception("Error resuming task %s: %s", task.task_id, exc)
                task.status = TaskStatus.FAILED
                task.error = str(exc)
            finally:
                self.task_registry.resources.release(task.task_id)

        thread = threading.Thread(
            target=_worker,
            daemon=True,
            name=f"avi-resume-{task.task_id[:8]}",
        )
        task.worker_thread = thread
        thread.start()

        return RuntimeResponse(
            text=f"▶ Resumed task: '{task.goal}' in background.",
            is_background=True,
            task_id=task.task_id,
            task=task,
        )
