"""Agent orchestrator for general-purpose computer agent workflows."""

import logging
import re
import time
from typing import Any

from avi.agent.context import OrchestrationContext, StepRecord, TaskStatus
from avi.agent.events import EventDispatcher, ProgressEvent, ProgressEventType
from avi.agent.executor import AgentExecutor
from avi.agent.loop_guard import LoopGuard
from avi.agent.models import AssistantInput, Plan, PlanStep, StepStatus
from avi.agent.planner import AgentPlanner
from avi.agent.tool_selection import ToolSelector
from avi.capabilities import CapabilityRegistry, create_default_capability_registry
from avi.capabilities.models import ExecutionStatus
from avi.memory.manager import MemoryManager
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
        self.memory = memory_retriever if memory_retriever is not None else MemoryRetriever(database=self.db)
        self.planner = planner if planner is not None else AgentPlanner()
        self.tool_selector = tool_selector if tool_selector is not None else ToolSelector(registry=self.registry)
        self.executor = executor if executor is not None else AgentExecutor(
            registry=self.registry,
            safety_engine=self.safety_engine,
            database=self.db,
            loop_guard=self.loop_guard,
            event_dispatcher=self.events,
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
                selected_tools = self.tool_selector.select_capabilities(clean_prompt, memories=retrieved_memories, limit=3)
                # Check if any top tool matches specific intent keywords
                lower_p = clean_prompt.lower()
                if selected_tools and any(
                    term in lower_p for term in ("search", "find", "screenshot", "volume", "sound", "file", "folder", "window", "launch", "open")
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
                context.final_response = "I couldn't find a matching action or plan for that request."
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

            # 5. Execution Loop
            context.status = TaskStatus.EXECUTING
            plan_res = self.executor.execute_plan(plan, confirmed=confirmed)

            # Sync step records with executor outcomes
            for i, step in enumerate(plan.steps):
                if i < len(context.steps):
                    context.steps[i].status = step.status.value
                    if step.result:
                        context.steps[i].observation = step.result.data
                        context.steps[i].error = step.result.error

            if plan_res.status == ExecutionStatus.CONFIRMATION_REQUIRED:
                context.status = TaskStatus.PAUSED_FOR_CONFIRMATION
                context.final_response = plan_res.final_message
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.PAUSED_FOR_CONFIRMATION,
                        task_id=context.task_id,
                        message=plan_res.final_message,
                    )
                )
                return context

            if not plan_res.success:
                # Bounded replanning attempt
                if context.replan_count < context.max_replans:
                    context.replan_count += 1
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.REPLANNING,
                            task_id=context.task_id,
                            message="Plan failed; attempting bounded self-correction...",
                        )
                    )
                    # Check if loop guard prevents further execution
                    if "Loop detected" in str(plan_res.error):
                        context.status = TaskStatus.FAILED
                        context.record_error(str(plan_res.error))
                        context.final_response = f"Execution stopped: {plan_res.error}"
                    else:
                        context.status = TaskStatus.FAILED
                        err_str = plan_res.error or plan_res.final_message or "Task failed."
                        context.record_error(err_str)
                        context.final_response = plan_res.final_message or plan_res.error or "Task failed."
                else:
                    context.status = TaskStatus.FAILED
                    err_str = plan_res.error or plan_res.final_message or "Task failed."
                    context.record_error(err_str)
                    context.final_response = plan_res.final_message or plan_res.error or "Task failed."
            else:
                context.status = TaskStatus.COMPLETED
                context.final_response = plan_res.final_message

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
