"""Agent orchestrator for persistent, autonomous computer agent workflows."""

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

from avi.agent.context import OrchestrationContext, StepRecord, TaskStatus
from avi.agent.dynamic_loop import DynamicAgentLoop
from avi.agent.events import EventDispatcher, ProgressEvent, ProgressEventType
from avi.agent.executor import AgentExecutor
from avi.agent.experience import ExperienceStore
from avi.agent.goal_verification import GoalVerifier
from avi.agent.loop_guard import LoopGuard
from avi.agent.models import (
    AssistantInput,
    FailureCategory,
    Goal,
    GoalSegment,
    Plan,
    PlanStep,
    StepStatus,
)
from avi.agent.observation import ObservationManager
from avi.agent.planner import AgentPlanner
from avi.agent.reconciliation import EnvironmentReconciler, ReconciliationStatus
from avi.agent.tool_selection import ToolSelector
from avi.agent.tool_validator import ToolCallValidator
from avi.capabilities import CapabilityRegistry, create_default_capability_registry
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.memory.retriever import MemoryRetriever
from avi.safety.engine import SafetyEngine
from avi.storage.database import Database
from avi.storage.models import DurableTaskRecord

logger = logging.getLogger("avi.agent.orchestrator")


class AgentOrchestrator:
    """Jarvis-style computer agent orchestrator with persistence, reconciliation, and experience memory."""

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        safety_engine: SafetyEngine | None = None,
        database: Database | None = None,
        memory_retriever: MemoryRetriever | None = None,
        loop_guard: LoopGuard | None = None,
        planner: AgentPlanner | None = None,
        executor: AgentExecutor | None = None,
        event_dispatcher: EventDispatcher | None = None,
        tool_selector: ToolSelector | None = None,
        experience_store: ExperienceStore | None = None,
        reconciler: EnvironmentReconciler | None = None,
        goal_verifier: GoalVerifier | None = None,
        dynamic_loop: DynamicAgentLoop | None = None,
    ) -> None:
        self.db = database if database is not None else Database()
        self.safety_engine = safety_engine if safety_engine is not None else SafetyEngine()
        self.registry = registry if registry is not None else create_default_capability_registry()
        self.events = event_dispatcher if event_dispatcher is not None else EventDispatcher()
        self.loop_guard = loop_guard if loop_guard is not None else LoopGuard()
        self.memory = (
            memory_retriever if memory_retriever is not None else MemoryRetriever(database=self.db)
        )
        self.experience_store = (
            experience_store if experience_store is not None else ExperienceStore(db=self.db)
        )
        self.planner = (
            planner
            if planner is not None
            else AgentPlanner(experience_store=self.experience_store)
        )
        self.tool_selector = (
            tool_selector if tool_selector is not None else ToolSelector(registry=self.registry)
        )
        self.executor = (
            executor
            if executor is not None
            else AgentExecutor(
                registry=self.registry,
                safety_engine=self.safety_engine,
                database=self.db,
                loop_guard=self.loop_guard,
                event_dispatcher=self.events,
            )
        )
        self.reconciler = reconciler if reconciler is not None else EnvironmentReconciler()
        self.goal_verifier = goal_verifier if goal_verifier is not None else GoalVerifier()
        self.observation_manager = ObservationManager(registry=self.registry)
        self.validator = ToolCallValidator(registry=self.registry, safety_engine=self.safety_engine)
        self.dynamic_loop = (
            dynamic_loop
            if dynamic_loop is not None
            else DynamicAgentLoop(
                registry=self.registry,
                safety_engine=self.safety_engine,
                executor=self.executor,
                validator=self.validator,
                observation_manager=self.observation_manager,
                loop_guard=self.loop_guard,
                event_dispatcher=self.events,
                goal_verifier=self.goal_verifier,
                memory_retriever=self.memory,
                experience_store=self.experience_store,
            )
        )
        self._active_tasks: dict[str, OrchestrationContext] = {}
        self._cancellation_tokens: dict[str, threading.Event] = {}
        self._pause_tokens: dict[str, threading.Event] = {}

    def can_handle(self, prompt: str) -> bool:
        """Check whether the agent orchestrator can create a plan or handle the prompt."""
        clean = prompt.strip()
        if not clean:
            return False
        action, _ = self._detect_interruption(clean)
        if action:
            return True
        # Check fast paths that shouldn't enter the background orchestrator
        from avi.assistant.intents import AssistantIntentType, detect_assistant_intent
        intent = detect_assistant_intent(clean)
        if intent.intent_type in (
            AssistantIntentType.GREETING,
            AssistantIntentType.SMALL_TALK,
            AssistantIntentType.COURTESY,
            AssistantIntentType.CAPABILITIES,
            AssistantIntentType.DISK_SPACE,
            AssistantIntentType.RAM_USAGE,
            AssistantIntentType.MEMORY_TOTAL,
            AssistantIntentType.CPU_USAGE,
            AssistantIntentType.SYSTEM_INFO,
            AssistantIntentType.TIMER,
        ):
            return False

        if self.planner.create_plan(clean) is not None:
            return True
        goal = self.planner.decompose_goal(clean)
        if goal.segments and any(s.plan for s in goal.segments):
            return True
        lower = clean.lower()
        tokens = set(re.findall(r"\b\w+\b", lower))
        if tokens.intersection({"video", "vid", "vids", "videos"}) and tokens.intersection(
            {"play", "open", "watch", "latest", "newest"}
        ):
            return True
        # Multi-step or tool-use prompts eligible for dynamic planning
        if len(clean.split()) >= 3 or any(
            t in lower
            for t in (
                "find", "search", "move", "rename", "delete", "open", "launch",
                "save", "write", "duplicate", "largest", "chrome", "code", "pdf",
                "download", "document", "picture", "report", "url", "web"
            )
        ):
            return True
        return False

    def _detect_interruption(self, prompt: str) -> tuple[str | None, str | None]:
        """Detect pause/cancel/resume command and optional target task ID."""
        clean = prompt.strip().lower()
        m = re.match(r"^(pause|stop|hold on|wait)(?:\s+task)?(?:\s+([a-zA-Z0-9_-]+))?$", clean)
        if m:
            return "pause", m.group(2)
        m = re.match(r"^(cancel|abort)(?:\s+task)?(?:\s+([a-zA-Z0-9_-]+))?$", clean)
        if m:
            return "cancel", m.group(2)
        m = re.match(r"^(resume|continue)(?:\s+task)?(?:\s+([a-zA-Z0-9_-]+))?$", clean)
        if m:
            return "resume", m.group(2)
        return None, None

    def _checkpoint_durable_task(
        self,
        context: OrchestrationContext,
        goal: Goal | None = None,
    ) -> None:
        """Save or update durable task checkpoint in database."""
        try:
            task_data = {
                "context": context.to_dict(),
                "goal": goal.to_dict() if goal else (context.goal.to_dict() if hasattr(context.goal, "to_dict") and context.goal else None),
                "replan_count": context.replan_count,
                "error_details": context.error_details,
            }
            existing = self.db.get_durable_task(context.task_id)
            existing_ver = 0
            if existing:
                existing_ver = getattr(existing, "schema_version", None) or getattr(existing, "version", None) or 1
            version = int(existing_ver) + 1
            now_ts = datetime.now(timezone.utc).isoformat()

            norm_goal = (goal.normalized_goal if goal else context.user_prompt).strip().lower()

            record = DurableTaskRecord(
                task_id=context.task_id,
                session_id=context.session_id,
                user_goal=context.user_prompt,
                normalized_goal=norm_goal,
                status=context.status.value,
                current_step_index=context.current_step_index,
                total_steps=len(context.steps),
                plan_data=task_data,
                environment_snapshot={"observations": context.observation_history[-3:] if context.observation_history else []},
                error=context.error_details[-1] if context.error_details else None,
                version=version,
                created_at=existing.created_at if existing else now_ts,
                updated_at=now_ts,
            )
            self.db.save_durable_task(record)
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.STATE_PERSISTED,
                    task_id=context.task_id,
                    message=f"Persisted task checkpoint (v{version}).",
                    data={"version": version, "status": context.status.value},
                )
            )
        except Exception as exc:
            logger.warning("Failed to checkpoint durable task: %s", exc)

    def _record_task_experience(
        self,
        context: OrchestrationContext,
        success: bool,
        goal: Goal | None = None,
    ) -> None:
        """Record execution experience for future strategy recommendations."""
        try:
            norm_goal = (goal.normalized_goal if goal else context.user_prompt).strip().lower()
            strat = "primary"
            if context.attempted_strategies:
                last_s = context.attempted_strategies[-1]
                strat = last_s.get("strategy", "primary") if isinstance(last_s, dict) else str(last_s)

            cap_used = ""
            completed = context.completed_steps()
            if completed:
                cap_used = completed[-1].capability_name
            elif context.steps:
                cap_used = context.steps[0].capability_name

            fail_cat = None
            failed = context.failed_steps()
            if failed and failed[-1].failure_category:
                fail_cat = str(failed[-1].failure_category)

            self.experience_store.record_outcome(
                goal=norm_goal,
                strategy_name=strat,
                capability_used=cap_used,
                success=success,
                failure_category=fail_cat,
                context_tags=[cap_used] if cap_used else [],
                verification_result=success,
            )
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.EXPERIENCE_RECORDED,
                    task_id=context.task_id,
                    message=f"Recorded execution experience for strategy '{strat}'.",
                    data={"strategy": strat, "success": success},
                )
            )
        except Exception as exc:
            logger.warning("Failed to record task experience: %s", exc)

    def pause_task(self, task_id: str) -> OrchestrationContext | None:
        """Pause an active durable task."""
        if task_id in self._pause_tokens:
            self._pause_tokens[task_id].set()

        rec = self.db.get_durable_task(task_id)
        if not rec:
            return None
        self.db.update_durable_task_status(task_id, "paused")
        ctx = self._active_tasks.get(task_id)
        if ctx:
            ctx.status = TaskStatus.PAUSED
            ctx.final_response = f"Task {task_id} paused."
        else:
            ctx = OrchestrationContext(
                user_prompt=rec.user_goal,
                task_id=rec.task_id,
                session_id=rec.session_id,
                status=TaskStatus.PAUSED,
                final_response=f"Task {task_id} paused.",
            )
        self._checkpoint_durable_task(ctx)
        self.events.emit(
            ProgressEvent(
                event_type=ProgressEventType.TASK_PAUSED,
                task_id=task_id,
                message=f"Task {task_id} paused.",
            )
        )
        return ctx

    def cancel_task(self, task_id: str) -> OrchestrationContext | None:
        """Cancel an active or paused durable task."""
        if task_id in self._cancellation_tokens:
            self._cancellation_tokens[task_id].set()

        rec = self.db.get_durable_task(task_id)
        if not rec:
            return None
        self.db.update_durable_task_status(task_id, "cancelled")
        ctx = self._active_tasks.get(task_id)
        if ctx:
            ctx.status = TaskStatus.CANCELLED
            ctx.final_response = f"Task {task_id} cancelled."
        else:
            ctx = OrchestrationContext(
                user_prompt=rec.user_goal,
                task_id=rec.task_id,
                session_id=rec.session_id,
                status=TaskStatus.CANCELLED,
                final_response=f"Task {task_id} cancelled.",
            )
        self._checkpoint_durable_task(ctx)
        self.events.emit(
            ProgressEvent(
                event_type=ProgressEventType.TASK_CANCELLED,
                task_id=task_id,
                message=f"Task {task_id} cancelled.",
            )
        )
        return ctx

    def resume_task(self, task_id: str, confirmed: bool = False) -> OrchestrationContext:
        """Resume an interrupted or paused task with environment reconciliation."""
        rec = self.db.get_durable_task(task_id)
        if not rec:
            ctx = OrchestrationContext(user_prompt="", task_id=task_id, status=TaskStatus.FAILED)
            ctx.record_error(f"Durable task '{task_id}' not found.")
            ctx.final_response = f"Cannot resume task: task '{task_id}' not found."
            return ctx

        if rec.status in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
            ctx = OrchestrationContext(
                user_prompt=rec.user_goal,
                task_id=rec.task_id,
                status=TaskStatus(rec.status),
                final_response=f"Task is already {rec.status}.",
            )
            return ctx

        self.events.emit(
            ProgressEvent(
                event_type=ProgressEventType.TASK_RESUMED,
                task_id=task_id,
                message=f"Resuming durable task {task_id}...",
            )
        )

        # Restore context from plan_data
        p_data = rec.plan_data or {}
        ctx_data = p_data.get("context", {})
        context = OrchestrationContext(
            user_prompt=rec.user_goal,
            task_id=rec.task_id,
            session_id=rec.session_id,
            status=TaskStatus.RUNNING,
            current_step_index=rec.current_step_index,
        )

        goal_data = p_data.get("goal")
        goal = Goal.from_dict(goal_data) if goal_data else None
        context.goal = goal

        # Reconstruct PlanSteps
        all_steps = []
        raw_steps = ctx_data.get("steps", [])
        for s in raw_steps:
            st_val = s.get("status", "pending")
            status_enum = StepStatus.PENDING
            for val in StepStatus:
                if val.value == st_val:
                    status_enum = val
                    break
            all_steps.append(
                PlanStep(
                    step_id=s.get("step_index", 0),
                    capability_name=s.get("capability_name", ""),
                    arguments=s.get("args") or s.get("input_data") or {},
                    description=s.get("description", ""),
                    status=status_enum,
                    verified=bool(s.get("verification_result", False)),
                )
            )

        completed_plan_steps = [s for s in all_steps if s.status == StepStatus.SUCCESS or s.verified]
        pending_plan_steps = [s for s in all_steps if s not in completed_plan_steps]

        # Run environment reconciliation
        reconcile_report = self.reconciler.reconcile(completed_plan_steps, pending_plan_steps)
        self.events.emit(
            ProgressEvent(
                event_type=ProgressEventType.GOAL_RECONCILED,
                task_id=task_id,
                message=f"Reconciled environment state: {reconcile_report.status.value}",
                data=reconcile_report.to_dict(),
            )
        )

        if reconcile_report.status == ReconciliationStatus.ALREADY_COMPLETE:
            context.status = TaskStatus.COMPLETED
            context.final_response = "Resumed task verified: all actions already completed in environment."
            self._checkpoint_durable_task(context, goal)
            self._record_task_experience(context, success=True, goal=goal)
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.GOAL_COMPLETED,
                    task_id=task_id,
                    message=context.final_response,
                )
            )
            return context

        # Filter out satisfied steps
        if reconcile_report.satisfied_steps:
            for s in pending_plan_steps:
                if s.step_id in reconcile_report.satisfied_steps:
                    s.status = StepStatus.SUCCESS
                    s.verified = True
                    completed_plan_steps.append(s)
            pending_plan_steps = [s for s in pending_plan_steps if s.step_id not in reconcile_report.satisfied_steps]

        # Build resumed plan from remaining steps
        if not pending_plan_steps and not reconcile_report.conflicting_steps:
            context.status = TaskStatus.COMPLETED
            context.final_response = "All steps completed."
            self._checkpoint_durable_task(context, goal)
            return context

        if reconcile_report.status == ReconciliationStatus.CONFLICTING or not pending_plan_steps:
            from avi.agent.diagnosis import DiagnosisResult
            diag = DiagnosisResult(
                category=FailureCategory.ENVIRONMENT_ERROR,
                root_cause=f"Reconciliation conflict: {reconcile_report.details}",
                suggested_recovery="Re-evaluate environment and rebuild plan",
                recoverable=True,
            )
            resumed_plan = self.planner.replan(
                goal=context.user_prompt,
                completed_steps=completed_plan_steps,
                failed_step=completed_plan_steps[-1] if completed_plan_steps else PlanStep(step_id=0, capability_name="reconciliation"),
                diagnosis=diag,
            )
        else:
            resumed_plan = Plan(
                user_goal=context.user_prompt,
                steps=pending_plan_steps,
                max_steps=len(pending_plan_steps) + 2,
            )

        if not resumed_plan or not resumed_plan.steps:
            context.status = TaskStatus.FAILED
            context.final_response = "Unable to create continuation plan for resumed task."
            self._checkpoint_durable_task(context, goal)
            return context

        context.steps = [
            StepRecord(
                step_index=s.step_id,
                capability_name=s.capability_name,
                args=s.arguments,
                description=s.description,
            )
            for s in resumed_plan.steps
        ]
        context.status = TaskStatus.EXECUTING
        self._checkpoint_durable_task(context, goal)

        plan_res = self.executor.execute_plan(resumed_plan, confirmed=confirmed)
        if plan_res.success:
            context.status = TaskStatus.COMPLETED
            context.final_response = plan_res.final_message
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.GOAL_COMPLETED,
                    task_id=task_id,
                    message=plan_res.final_message,
                )
            )
        else:
            context.status = TaskStatus.FAILED
            context.final_response = plan_res.final_message or plan_res.error or "Resumed task execution failed."
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.GOAL_FAILED,
                    task_id=task_id,
                    message=context.final_response,
                )
            )

        self._checkpoint_durable_task(context, goal)
        self._record_task_experience(context, success=(context.status == TaskStatus.COMPLETED), goal=goal)
        return context

    def run(
        self,
        user_input: str | AssistantInput,
        confirmed: bool = False,
        session_id: str = "",
        cancellation_token: threading.Event | None = None,
        pause_token: threading.Event | None = None,
    ) -> OrchestrationContext:
        """Execute an end-to-end agent task with guaranteed bounded terminal state and persistence."""
        if isinstance(user_input, AssistantInput):
            prompt = user_input.text
            confirmed = confirmed or user_input.confirmed
            session_id = session_id or user_input.session_id
        else:
            prompt = str(user_input)

        clean_prompt = prompt.strip()

        # Check for interruption commands (pause/cancel/resume)
        action, target_id = self._detect_interruption(clean_prompt)
        if action:
            if action == "pause":
                tid = target_id or (next(iter(self._active_tasks.keys())) if self._active_tasks else None)
                if not tid:
                    running_tasks = self.db.list_durable_tasks(status="running")
                    if running_tasks:
                        tid = running_tasks[0].task_id
                if tid:
                    res = self.pause_task(tid)
                    if res:
                        return res
            elif action == "cancel":
                tid = target_id or (next(iter(self._active_tasks.keys())) if self._active_tasks else None)
                if not tid:
                    running_tasks = self.db.list_durable_tasks(status="running")
                    if running_tasks:
                        tid = running_tasks[0].task_id
                if tid:
                    res = self.cancel_task(tid)
                    if res:
                        return res
            elif action == "resume":
                tid = target_id
                if not tid:
                    paused_tasks = self.db.list_durable_tasks(status="paused")
                    if not paused_tasks:
                        paused_tasks = self.db.list_durable_tasks(status="paused_for_confirmation")
                    if paused_tasks:
                        tid = paused_tasks[0].task_id
                if tid:
                    return self.resume_task(tid, confirmed=confirmed)

        context = OrchestrationContext(
            user_prompt=prompt,
            session_id=session_id,
        )
        context.durable_task_id = context.task_id
        self._active_tasks[context.task_id] = context

        canc_token = cancellation_token or threading.Event()
        pau_token = pause_token or threading.Event()
        self._cancellation_tokens[context.task_id] = canc_token
        self._pause_tokens[context.task_id] = pau_token

        try:
            # 1. Lifecycle start
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.TASK_STARTED,
                    task_id=context.task_id,
                    message=f"Starting task: {prompt}",
                    data={"prompt": prompt},
                )
            )

            if not clean_prompt:
                context.status = TaskStatus.COMPLETED
                context.final_response = ""
                self._checkpoint_durable_task(context)
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.TASK_COMPLETED,
                        task_id=context.task_id,
                        message="Empty prompt completed.",
                    )
                )
                return context

            # 2. Retrieval: Relevant memories and preferences
            retrieved_memories = self.memory.search(clean_prompt, limit=5)
            context.memories = retrieved_memories
            mem_context = self.memory.format_context(retrieved_memories)
            if mem_context:
                context.metadata["memory_context"] = mem_context

            # 3. Planning & Goal Decomposition
            context.status = TaskStatus.PLANNING
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.PLANNING,
                    task_id=context.task_id,
                    message="Synthesizing execution plan...",
                )
            )

            # Determine primary plan
            plan = self.planner.create_plan(clean_prompt, context=context.metadata)

            # Decompose goal if planner supports it
            goal = None
            if plan is not None:
                goal = Goal(
                    user_goal=clean_prompt,
                    normalized_goal=clean_prompt.strip().lower(),
                    segments=[
                        GoalSegment(
                            segment_id="seg_1",
                            title=clean_prompt,
                            description=clean_prompt,
                            plan=plan,
                        )
                    ],
                )
            elif hasattr(self.planner, "decompose_goal"):
                try:
                    decomposed = self.planner.decompose_goal(clean_prompt, context=context.metadata)
                    if isinstance(decomposed, Goal):
                        goal = decomposed
                        if goal.segments and goal.segments[0].plan:
                            plan = goal.segments[0].plan
                except Exception:
                    goal = None

            if goal is None:
                goal = Goal(
                    user_goal=clean_prompt,
                    normalized_goal=clean_prompt.strip().lower(),
                )
            context.goal = goal

            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.GOAL_CREATED,
                    task_id=context.task_id,
                    message=f"Goal created with {len(goal.segments)} milestone segment(s).",
                    data={"segments_count": len(goal.segments)},
                )
            )

            # Check dynamic tool selection if deterministic planner has no match
            if plan is None:
                selected_tools = self.tool_selector.select_capabilities(
                    clean_prompt, memories=retrieved_memories, limit=3
                )
                lower_p = clean_prompt.lower()
                if selected_tools and any(
                    term in lower_p
                    for term in (
                        "search",
                        "find",
                        "screenshot",
                        "volume",
                        "sound",
                        "file",
                        "folder",
                        "window",
                        "launch",
                        "open",
                    )
                ):
                    top_tool = selected_tools[0]
                    top_name = top_tool.get("name", "")
                    top_desc = top_tool.get("description", "")
                    args: dict[str, Any] = {}
                    if "search" in top_name:
                        args["query"] = clean_prompt
                    elif "apps.open" in top_name:
                        args["app"] = clean_prompt
                    elif "desktop.open_url" in top_name:
                        args["url"] = clean_prompt

                    if args:
                        plan = Plan(
                            user_goal=clean_prompt,
                            steps=[
                                PlanStep(
                                    step_id=1,
                                    capability_name=top_name,
                                    arguments=args,
                                    description=top_desc,
                                )
                            ],
                        )

            if plan is None or not plan.steps:
                # Engage dynamic autonomous tool-use agent loop
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.PLANNING,
                        task_id=context.task_id,
                        message="Engaging dynamic autonomous tool planner...",
                    )
                )
                dyn_res = self.dynamic_loop.run(
                    clean_prompt,
                    context=context,
                    confirmed=confirmed,
                    cancellation_token=canc_token,
                    pause_token=pau_token,
                )
                context.status = dyn_res.status
                context.final_response = dyn_res.final_response
                context.steps = dyn_res.steps_executed
                if dyn_res.artifacts:
                    context.artifacts = dyn_res.artifacts
                if dyn_res.error:
                    context.error_details.append(dyn_res.error)

                self._checkpoint_durable_task(context, goal)
                self._record_task_experience(context, success=dyn_res.success, goal=goal)

                if dyn_res.status == TaskStatus.COMPLETED:
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.GOAL_COMPLETED,
                            task_id=context.task_id,
                            message=dyn_res.final_response,
                        )
                    )
                elif dyn_res.status == TaskStatus.PAUSED_FOR_CONFIRMATION:
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.CONFIRMATION_REQUIRED,
                            task_id=context.task_id,
                            message=dyn_res.final_response,
                        )
                    )
                elif dyn_res.status == TaskStatus.FAILED:
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.GOAL_FAILED,
                            task_id=context.task_id,
                            message=dyn_res.final_response,
                        )
                    )
                return context

            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.PLAN_CREATED,
                    task_id=context.task_id,
                    message=f"Plan ready ({len(plan.steps)} step{'s' if len(plan.steps) != 1 else ''}).",
                    data={"steps_count": len(plan.steps)},
                )
            )

            # Convert plan steps to context steps
            context.steps = [
                StepRecord(
                    step_index=s.step_id,
                    capability_name=s.capability_name,
                    args=s.arguments,
                    description=s.description,
                )
                for s in plan.steps
            ]

            for s in plan.steps:
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.CAPABILITY_SELECTED,
                        task_id=context.task_id,
                        step_index=s.step_id,
                        capability_name=s.capability_name,
                        message=f"Selected capability: {s.capability_name}",
                    )
                )

            # 4. Confirmation Pause check
            if plan.requires_confirmation and not confirmed:
                context.status = TaskStatus.PAUSED_FOR_CONFIRMATION
                context.final_response = plan.confirmation_prompt
                self._checkpoint_durable_task(context, goal)
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.PAUSED_FOR_CONFIRMATION,
                        task_id=context.task_id,
                        message=plan.confirmation_prompt,
                    )
                )
                return context

            # 5. Adaptive Execution & Replanning Loop
            context.status = TaskStatus.EXECUTING
            self._checkpoint_durable_task(context, goal)

            # Check if multi-segment goal execution is required
            if isinstance(goal, Goal) and goal.is_decomposed and len(goal.segments) > 1 and all(isinstance(s, GoalSegment) for s in goal.segments):
                # Execute milestone segments sequentially
                cumulative_completed: list[PlanStep] = []
                for seg_idx, segment in enumerate(goal.segments):
                    goal.current_segment_index = seg_idx
                    if not segment.plan:
                        continue
                    if canc_token.is_set():
                        context.status = TaskStatus.CANCELLED
                        context.final_response = "Task cancelled by user."
                        self._checkpoint_durable_task(context, goal)
                        self.events.emit(
                            ProgressEvent(
                                event_type=ProgressEventType.TASK_CANCELLED,
                                task_id=context.task_id,
                                message="Task cancelled by user.",
                            )
                        )
                        return context

                    if pau_token.is_set():
                        context.status = TaskStatus.PAUSED
                        context.final_response = "Task paused by user."
                        self._checkpoint_durable_task(context, goal)
                        self.events.emit(
                            ProgressEvent(
                                event_type=ProgressEventType.TASK_PAUSED,
                                task_id=context.task_id,
                                message="Task paused by user.",
                            )
                        )
                        return context

                    seg_res = self.executor.execute_plan(
                        segment.plan,
                        confirmed=confirmed,
                        cancellation_token=canc_token,
                        pause_token=pau_token,
                    )
                    segment.completed_steps = seg_res.completed_steps
                    cumulative_completed.extend(seg_res.completed_steps)
                    self._checkpoint_durable_task(context, goal)

                    if seg_res.status == ExecutionStatus.CANCELLED or canc_token.is_set():
                        context.status = TaskStatus.CANCELLED
                        context.final_response = "Task cancelled by user."
                        self._checkpoint_durable_task(context, goal)
                        self.events.emit(
                            ProgressEvent(
                                event_type=ProgressEventType.TASK_CANCELLED,
                                task_id=context.task_id,
                                message="Task cancelled by user.",
                            )
                        )
                        return context

                    if seg_res.status == ExecutionStatus.PAUSED or pau_token.is_set():
                        context.status = TaskStatus.PAUSED
                        context.final_response = "Task paused by user."
                        self._checkpoint_durable_task(context, goal)
                        self.events.emit(
                            ProgressEvent(
                                event_type=ProgressEventType.TASK_PAUSED,
                                task_id=context.task_id,
                                message="Task paused by user.",
                            )
                        )
                        return context

                    if not seg_res.success:
                        context.status = TaskStatus.FAILED
                        context.final_response = seg_res.final_message or seg_res.error or "Goal milestone failed."
                        self._checkpoint_durable_task(context, goal)
                        self._record_task_experience(context, success=False, goal=goal)
                        self.events.emit(
                            ProgressEvent(
                                event_type=ProgressEventType.GOAL_FAILED,
                                task_id=context.task_id,
                                message=context.final_response,
                            )
                        )
                        return context

                context.status = TaskStatus.COMPLETED
                context.final_response = "All goal milestones completed successfully."
                self._checkpoint_durable_task(context, goal)
                self._record_task_experience(context, success=True, goal=goal)
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.GOAL_COMPLETED,
                        task_id=context.task_id,
                        message=context.final_response,
                    )
                )
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.TASK_COMPLETED,
                        task_id=context.task_id,
                        message=context.final_response,
                        data={"status": context.status.value},
                    )
                )
                return context

            # Single plan adaptive loop
            current_plan = plan
            cumulative_completed_steps: list[PlanStep] = []

            while True:
                if canc_token.is_set():
                    context.status = TaskStatus.CANCELLED
                    context.final_response = "Task cancelled by user."
                    self._checkpoint_durable_task(context, goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.TASK_CANCELLED,
                            task_id=context.task_id,
                            message="Task cancelled by user.",
                        )
                    )
                    return context

                if pau_token.is_set():
                    context.status = TaskStatus.PAUSED
                    context.final_response = "Task paused by user."
                    self._checkpoint_durable_task(context, goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.TASK_PAUSED,
                            task_id=context.task_id,
                            message="Task paused by user.",
                        )
                    )
                    return context

                plan_res = self.executor.execute_plan(
                    current_plan,
                    confirmed=confirmed,
                    cancellation_token=canc_token,
                    pause_token=pau_token,
                )

                if plan_res.status == ExecutionStatus.CANCELLED or canc_token.is_set():
                    context.status = TaskStatus.CANCELLED
                    context.final_response = "Task cancelled by user."
                    self._checkpoint_durable_task(context, goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.TASK_CANCELLED,
                            task_id=context.task_id,
                            message="Task cancelled by user.",
                        )
                    )
                    return context

                if plan_res.status == ExecutionStatus.PAUSED or pau_token.is_set():
                    context.status = TaskStatus.PAUSED
                    context.final_response = "Task paused by user."
                    self._checkpoint_durable_task(context, goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.TASK_PAUSED,
                            task_id=context.task_id,
                            message="Task paused by user.",
                        )
                    )
                    return context

                # Sync step records with executor outcomes
                for s in current_plan.steps:
                    idx = s.step_id - 1
                    if 0 <= idx < len(context.steps):
                        context.steps[idx].status = s.status.value
                        context.steps[idx].input_data = s.arguments
                        if s.result:
                            context.steps[idx].output_data = (
                                s.result.data if isinstance(s.result.data, dict) else {}
                            )
                            context.steps[idx].observation = s.result.data
                            context.steps[idx].error = s.result.error
                        context.steps[idx].verification_result = getattr(s, "verified", None)
                        context.steps[idx].duration_ms = getattr(s, "duration_ms", 0.0)
                        art = getattr(s, "artifact_path", None)
                        if art:
                            context.steps[idx].artifact_path = art
                            if art not in context.steps[idx].artifacts:
                                context.steps[idx].artifacts.append(art)

                self._checkpoint_durable_task(context, goal)

                if plan_res.status == ExecutionStatus.CONFIRMATION_REQUIRED:
                    context.status = TaskStatus.PAUSED_FOR_CONFIRMATION
                    context.final_response = plan_res.final_message
                    self._checkpoint_durable_task(context, goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.PAUSED_FOR_CONFIRMATION,
                            task_id=context.task_id,
                            message=plan_res.final_message,
                            data={
                                "pending_step": plan_res.pending_step.to_dict()
                                if plan_res.pending_step
                                else {}
                            },
                        )
                    )
                    return context

                if plan_res.success:
                    cumulative_completed_steps.extend(plan_res.completed_steps)
                    context.status = TaskStatus.COMPLETED
                    context.final_response = plan_res.final_message
                    self._checkpoint_durable_task(context, goal)
                    self._record_task_experience(context, success=True, goal=goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.GOAL_COMPLETED,
                            task_id=context.task_id,
                            message=context.final_response,
                        )
                    )
                    break

                # Execution failed: track partial progress
                cumulative_completed_steps.extend(plan_res.completed_steps)
                err_str = plan_res.error or plan_res.final_message or "Task failed."
                context.record_error(err_str)

                # If loop detected or max replans reached, halt safely
                if "Loop detected" in str(plan_res.error):
                    context.status = TaskStatus.FAILED
                    context.final_response = f"Execution stopped: {plan_res.error}"
                    self._checkpoint_durable_task(context, goal)
                    self._record_task_experience(context, success=False, goal=goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.GOAL_FAILED,
                            task_id=context.task_id,
                            message=context.final_response,
                        )
                    )
                    break

                if context.replan_count >= context.max_replans:
                    context.status = TaskStatus.FAILED
                    context.final_response = plan_res.final_message or err_str
                    self._checkpoint_durable_task(context, goal)
                    self._record_task_experience(context, success=False, goal=goal)
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.GOAL_FAILED,
                            task_id=context.task_id,
                            message=context.final_response,
                        )
                    )
                    break

                # Attempt bounded adaptive replanning
                context.replan_count += 1
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.REPLANNING,
                        task_id=context.task_id,
                        message="Plan failed; attempting bounded self-correction...",
                        data={"replan_count": context.replan_count},
                    )
                )

                failed_step_id = plan_res.data.get("failed_step") if plan_res.data else None
                failed_step = next(
                    (s for s in current_plan.steps if s.step_id == failed_step_id),
                    current_plan.steps[-1] if current_plan.steps else None,
                )
                if not failed_step:
                    context.status = TaskStatus.FAILED
                    context.final_response = plan_res.final_message or err_str
                    self._checkpoint_durable_task(context, goal)
                    self._record_task_experience(context, success=False, goal=goal)
                    break

                from avi.agent.diagnosis import DiagnosisResult, FailureDiagnoser

                diag_data = (plan_res.data or {}).get("diagnosis")
                if diag_data and isinstance(diag_data, dict):
                    diagnosis = DiagnosisResult(
                        category=FailureCategory(diag_data.get("category", "execution_error")),
                        root_cause=diag_data.get("root_cause", ""),
                        suggested_recovery=diag_data.get("suggested_recovery", ""),
                        recoverable=diag_data.get("recoverable", True),
                    )
                else:
                    diagnosis = FailureDiagnoser().diagnose(
                        failed_step,
                        failed_step.result
                        or CapabilityResult(
                            success=False, status=ExecutionStatus.FAILED, error=err_str
                        ),
                    )

                attempted_strategies = [
                    s.get("strategy") for s in context.attempted_strategies if isinstance(s, dict)
                ]
                new_plan = self.planner.replan(
                    goal=context.user_prompt,
                    completed_steps=cumulative_completed_steps,
                    failed_step=failed_step,
                    diagnosis=diagnosis,
                    attempted_strategies=attempted_strategies,
                )

                if (
                    not isinstance(new_plan, Plan)
                    or not new_plan.steps
                    or not all(isinstance(s, PlanStep) for s in new_plan.steps)
                ):
                    context.status = TaskStatus.FAILED
                    context.final_response = plan_res.final_message or err_str
                    self._checkpoint_durable_task(context, goal)
                    self._record_task_experience(context, success=False, goal=goal)
                    break

                # Register adapted plan and append steps to context
                context.attempted_strategies.append(
                    {
                        "strategy": new_plan.strategy_name,
                        "replan_count": context.replan_count,
                    }
                )
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.PLAN_ADAPTED,
                        task_id=context.task_id,
                        message=f"Adapted plan generated using strategy '{new_plan.strategy_name}' ({len(new_plan.steps)} steps).",
                        data=new_plan.to_dict(),
                    )
                )
                for s in new_plan.steps:
                    context.steps.append(
                        StepRecord(
                            step_index=s.step_id,
                            capability_name=s.capability_name,
                            args=s.arguments,
                            description=s.description,
                            status="pending",
                        )
                    )
                current_plan = new_plan

            # 6. Lifecycle completion
            term_event_type = (
                ProgressEventType.TASK_FAILED
                if context.status == TaskStatus.FAILED
                else ProgressEventType.TASK_COMPLETED
            )
            self.events.emit(
                ProgressEvent(
                    event_type=term_event_type,
                    task_id=context.task_id,
                    message=context.final_response,
                    data={"status": context.status.value},
                )
            )
            return context

        except Exception as exc:
            logger.exception("Unhandled exception during agent orchestration: %s", exc)
            context.status = TaskStatus.FAILED
            context.record_error(str(exc))
            context.final_response = f"Error executing task: {exc}"
            self._checkpoint_durable_task(context)
            self._record_task_experience(context, success=False)
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.TASK_FAILED,
                    task_id=context.task_id,
                    message=context.final_response,
                    data={"error": str(exc)},
                )
            )
            return context
        finally:
            self._active_tasks.pop(context.task_id, None)
            self._cancellation_tokens.pop(context.task_id, None)
            self._pause_tokens.pop(context.task_id, None)

    def handle(self, prompt: str, confirmed: bool = False) -> str:
        """Simple invocation returning user-facing synthesized message."""
        ctx = self.run(prompt, confirmed=confirmed)
        return ctx.final_response
