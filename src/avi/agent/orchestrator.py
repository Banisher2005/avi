"""Agent orchestrator for general-purpose computer agent workflows."""

import logging
import re
from typing import Any

from avi.agent.context import OrchestrationContext, StepRecord, TaskStatus
from avi.agent.events import EventDispatcher, ProgressEvent, ProgressEventType
from avi.agent.executor import AgentExecutor
from avi.agent.loop_guard import LoopGuard
from avi.agent.models import AssistantInput, FailureCategory, Plan, PlanStep
from avi.agent.planner import AgentPlanner
from avi.agent.tool_selection import ToolSelector
from avi.capabilities import CapabilityRegistry, create_default_capability_registry
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.memory.retriever import MemoryRetriever
from avi.safety.engine import SafetyEngine
from avi.storage.database import Database

logger = logging.getLogger("avi.agent.orchestrator")


class AgentOrchestrator:
    """Jarvis-style computer agent orchestrator with loop prevention, retrieval, and verification."""

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
    ) -> None:
        self.db = database if database is not None else Database()
        self.safety_engine = safety_engine if safety_engine is not None else SafetyEngine()
        self.registry = registry if registry is not None else create_default_capability_registry()
        self.events = event_dispatcher if event_dispatcher is not None else EventDispatcher()
        self.loop_guard = loop_guard if loop_guard is not None else LoopGuard()
        self.memory = (
            memory_retriever if memory_retriever is not None else MemoryRetriever(database=self.db)
        )
        self.planner = planner if planner is not None else AgentPlanner()
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

    def can_handle(self, prompt: str) -> bool:
        """Check whether the agent orchestrator can create a plan or handle the prompt."""
        clean = prompt.strip()
        if not clean:
            return False
        if self.planner.create_plan(clean) is not None:
            return True
        lower = clean.lower()
        tokens = set(re.findall(r"\b\w+\b", lower))
        if tokens.intersection({"video", "vid", "vids", "videos"}) and tokens.intersection(
            {"play", "open", "watch", "latest", "newest"}
        ):
            return True
        return False

    def run(
        self,
        user_input: str | AssistantInput,
        confirmed: bool = False,
        session_id: str = "",
    ) -> OrchestrationContext:
        """Execute an end-to-end agent task with guaranteed bounded terminal state."""
        if isinstance(user_input, AssistantInput):
            prompt = user_input.text
            confirmed = confirmed or user_input.confirmed
            session_id = session_id or user_input.session_id
        else:
            prompt = str(user_input)

        context = OrchestrationContext(
            user_prompt=prompt,
            session_id=session_id,
        )

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

            clean_prompt = prompt.strip()
            if not clean_prompt:
                context.status = TaskStatus.COMPLETED
                context.final_response = ""
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

            # 3. Planning
            context.status = TaskStatus.PLANNING
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.PLANNING,
                    task_id=context.task_id,
                    message="Synthesizing execution plan...",
                )
            )

            # A. Check deterministic planner first
            plan = self.planner.create_plan(clean_prompt, context=context.metadata)

            # B. Check dynamic tool selection if deterministic planner has no match
            if plan is None:
                selected_tools = self.tool_selector.select_capabilities(
                    clean_prompt, memories=retrieved_memories, limit=3
                )
                # Check if any top tool matches specific intent keywords
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
                    # Synthesize a single-step fallback plan
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

            if plan is None:
                # Unhandled prompt or conversational query
                context.status = TaskStatus.COMPLETED
                context.final_response = (
                    "I couldn't find a matching action or plan for that request."
                )
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.TASK_COMPLETED,
                        task_id=context.task_id,
                        message="Task completed with no executable plan.",
                    )
                )
                return context

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
            current_plan = plan
            cumulative_completed_steps: list[PlanStep] = []

            while True:
                plan_res = self.executor.execute_plan(current_plan, confirmed=confirmed)

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

                if plan_res.status == ExecutionStatus.CONFIRMATION_REQUIRED:
                    context.status = TaskStatus.PAUSED_FOR_CONFIRMATION
                    context.final_response = plan_res.final_message
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
                    break

                # Execution failed: track partial progress
                cumulative_completed_steps.extend(plan_res.completed_steps)
                err_str = plan_res.error or plan_res.final_message or "Task failed."
                context.record_error(err_str)

                # If loop detected or max replans reached, halt safely
                if "Loop detected" in str(plan_res.error):
                    context.status = TaskStatus.FAILED
                    context.final_response = f"Execution stopped: {plan_res.error}"
                    break

                if context.replan_count >= context.max_replans:
                    context.status = TaskStatus.FAILED
                    context.final_response = plan_res.final_message or err_str
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
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.TASK_FAILED,
                    task_id=context.task_id,
                    message=context.final_response,
                    data={"error": str(exc)},
                )
            )
            return context

    def handle(self, prompt: str, confirmed: bool = False) -> str:
        """Simple invocation returning user-facing synthesized message."""
        ctx = self.run(prompt, confirmed=confirmed)
        return ctx.final_response
