"""Assistant Orchestrator subsystem for AVI.

Central coordination layer for:
- User intent recognition (greetings, system info, timers, apps, URLs)
- Tool-first selection and natural-language synthesis
- Native assistant action dispatch (Application launching, URLs, timers)
- FastPath deterministic matching
- Provider-agnostic AI reasoning and tool-selection
- Safe shell execution fallback through SafetyEngine
"""

import time
from typing import Any

from avi.actions.system import OpenAppAction, OpenDirAction, OpenFileAction, OpenUrlAction
from avi.actions.timer import TimerAction
from avi.apps.resolver import ApplicationResolver
from avi.assistant.intents import (
    AssistantIntentType,
    detect_assistant_intent,
)
from avi.assistant.synthesizer import (
    format_disk_space_conversational,
    format_processes_conversational,
    format_processes_summary,
    format_system_info_conversational,
    get_memory_summary_conversational,
)
from avi.config import Config
from avi.core.router import Router
from avi.orchestrator.models import ConversationHistory, OrchestratorResult
from avi.providers.models import ResponseMetrics
from avi.safety.engine import SafetyEngine
from avi.tools.registry import ToolRegistry, create_default_registry


class AssistantOrchestrator:
    """Central orchestrator mediating between user requests, tools, actions, and AI providers."""

    def __init__(
        self,
        config: Config,
        router: Router | None = None,
        app_resolver: ApplicationResolver | None = None,
        tools: ToolRegistry | None = None,
        safety: SafetyEngine | None = None,
        history: ConversationHistory | None = None,
        capabilities: Any | None = None,
        planner: Any | None = None,
        executor: Any | None = None,
    ) -> None:
        self.config = config
        self.tools = tools or create_default_registry()
        self.safety = safety or SafetyEngine()
        self.app_resolver = app_resolver or ApplicationResolver()
        self.history = history or ConversationHistory()
        self.router = router or Router(
            config=config,
            tools=self.tools,
            safety=self.safety,
        )
        from avi.agent import AgentExecutor, AgentPlanner
        from avi.capabilities import CapabilityRegistry, create_default_capability_registry

        self.capabilities: CapabilityRegistry = capabilities or create_default_capability_registry(
            tools=self.tools,
            resolver=self.app_resolver,
        )
        self.planner: AgentPlanner = planner or AgentPlanner()
        self.executor: AgentExecutor = executor or AgentExecutor(
            registry=self.capabilities,
            safety_engine=self.safety,
        )

    def is_assistant_request(self, prompt: str) -> bool:
        """Determine if a prompt should be routed to native assistant capabilities rather than shell fallback."""
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            return False

        # 1. Native assistant intent recognition (greetings, courtesies, tools, actions, volume, screenshot)
        intent = detect_assistant_intent(normalized_prompt, last_turn=self.history.last_turn)
        if intent.intent_type != AssistantIntentType.UNKNOWN:
            return True

        # 2. Screen observation intent
        import re

        if re.search(
            r"\b(?:what(?:'s|\s+is)\s+on\s+my\s+screen|read\s+(?:what(?:'s|\s+is)\s+on\s+my\s+screen|my\s+screen)|what\s+am\s+i\s+looking\s+at)\b",
            normalized_prompt.lower(),
        ):
            return True

        # 3. Pending confirmation for multi-step plan
        if (
            normalized_prompt.lower() in ("yes", "y", "confirm", "proceed", "do it")
            and self.history.last_turn
            and self.history.last_turn.plan
            and getattr(self.history.last_turn.plan, "requires_confirmation", False)
        ):
            return True

        # 4. Capability agent planner (composite or single-step plans)
        if self.planner.create_plan(normalized_prompt) is not None:
            return True

        # 5. Desktop domain boundary: prevent desktop action terms from falling through to shell generator
        desktop_keywords = {
            "volume",
            "volme",
            "vol",
            "sound",
            "audio",
            "screenshot",
            "screnshot",
            "screeshot",
            "mute",
            "unmute",
            "youtube",
            "youtub",
            "yotube",
        }
        lower_tokens = set(re.findall(r"\b\w+\b", normalized_prompt.lower()))
        if lower_tokens.intersection(desktop_keywords):
            return True

        return False

    def handle(
        self,
        prompt: str,
        context: Any | None = None,
        auto_execute_actions: bool = True,
    ) -> OrchestratorResult:
        """Process user input through the assistant hierarchy."""
        t0 = time.perf_counter()
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            return OrchestratorResult(text="")

        # ── Step 1: Detect native assistant intent ───────────────────────
        intent = detect_assistant_intent(normalized_prompt, last_turn=self.history.last_turn)
        result: OrchestratorResult | None = None

        # A. Conversational greetings
        if intent.intent_type == AssistantIntentType.GREETING:
            result = OrchestratorResult(
                text="Hello! How can I help?",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # B. Capabilities and help
        elif intent.intent_type == AssistantIntentType.CAPABILITIES:
            result = OrchestratorResult(
                text=(
                    "I can help with your computer, including system information, applications, "
                    "files, timers, and other tasks. I can also use an AI model when a request "
                    "needs reasoning or conversation."
                ),
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # C. Conversational small talk
        elif intent.intent_type == AssistantIntentType.SMALL_TALK:
            result = OrchestratorResult(
                text="I'm ready! Ask me about your system, open apps, set a timer, or ask a question.",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # D. Conversational courtesy
        elif intent.intent_type == AssistantIntentType.COURTESY:
            result = OrchestratorResult(
                text="You're welcome! Let me know if you need anything else.",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # E. Clarification for ambiguous requests
        elif intent.intent_type == AssistantIntentType.CLARIFICATION:
            msg = intent.extra.get(
                "message",
                "Could you please clarify what you would like me to do?",
            )
            result = OrchestratorResult(
                text=msg,
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # F. Invalid request guidance (e.g. invalid timer)
        elif intent.intent_type == AssistantIntentType.INVALID_REQUEST:
            msg = intent.extra.get(
                "message",
                "Please check your request syntax and try again.",
            )
            result = OrchestratorResult(
                text=msg,
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # G. Native Tool: Disk Space
        elif intent.intent_type == AssistantIntentType.DISK_SPACE:
            tool = self.tools.get("system.disk_usage")
            if tool:
                tool_res = tool.execute(path="/")
                text = format_disk_space_conversational(tool_res)
                if intent.extra.get("follow_up") == "disk_breakdown":
                    text = f"{text} Primary storage is allocated across system libraries and your home directory."
                result = OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text="Disk usage tool is not available.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # H. Conversational Memory Overview
        elif intent.intent_type == AssistantIntentType.MEMORY_TOTAL:
            text = get_memory_summary_conversational()
            result = OrchestratorResult(
                text=text,
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # I. Native Tool: Memory / RAM Process Usage
        elif intent.intent_type == AssistantIntentType.RAM_USAGE:
            tool = self.tools.get("system.processes")
            if tool:
                tool_res = tool.execute(limit=5, sort_by="memory")
                text = format_processes_conversational(tool_res, sort_by="memory")
                result = OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text="Process inspection tool is not available.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # J. Native Tool: CPU
        elif intent.intent_type == AssistantIntentType.CPU_USAGE:
            tool = self.tools.get("system.processes")
            if tool:
                tool_res = tool.execute(limit=5, sort_by="cpu")
                text = format_processes_conversational(tool_res, sort_by="cpu")
                result = OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text="Process inspection tool is not available.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # K. Native Tool: Running processes list (conversational summary)
        elif intent.intent_type == AssistantIntentType.PROCESSES:
            tool = self.tools.get("system.processes")
            if tool:
                tool_res = tool.execute(limit=10, sort_by="memory")
                text = format_processes_summary(tool_res)
                result = OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text="Process inspection tool is not available.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # L. Native Tool: System info
        elif intent.intent_type == AssistantIntentType.SYSTEM_INFO:
            tool = self.tools.get("system.system_info")
            if tool:
                tool_res = tool.execute()
                text = format_system_info_conversational(tool_res)
                result = OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text="System information tool is not available.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # M. Native Action: Timer
        elif intent.intent_type == AssistantIntentType.TIMER:
            dur = intent.extra.get("duration_seconds", 0.0)
            label = intent.extra.get("label", "")
            action = TimerAction(duration_seconds=dur, label=label)
            if auto_execute_actions:
                start_msg = action.start_message
                act_res = action.execute()
                combined = f"{start_msg}\n{act_res.message}" if start_msg else act_res.message
                result = OrchestratorResult(
                    text=combined,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text=action.start_message,
                    action=action,
                    context=context,
                )

        # N. Native Action: Open URL
        elif intent.intent_type == AssistantIntentType.OPEN_URL:
            action = OpenUrlAction(url=intent.target)
            if auto_execute_actions:
                act_res = action.execute()
                result = OrchestratorResult(
                    text=act_res.message,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text=f"Ready to open URL: {intent.target}",
                    action=action,
                    context=context,
                )

        # O. Native Action: Open File
        elif intent.intent_type == AssistantIntentType.OPEN_FILE:
            action = OpenFileAction(path=intent.target)
            if auto_execute_actions:
                act_res = action.execute()
                result = OrchestratorResult(
                    text=act_res.message,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text=f"Ready to open file: {intent.target}",
                    action=action,
                    context=context,
                )

        # P. Native Action: Open Directory
        elif intent.intent_type == AssistantIntentType.OPEN_DIR:
            action = OpenDirAction(path=intent.target)
            if auto_execute_actions:
                act_res = action.execute()
                result = OrchestratorResult(
                    text=act_res.message,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                result = OrchestratorResult(
                    text=f"Ready to open directory: {intent.target}",
                    action=action,
                    context=context,
                )

        # Q. Native Action: Open Application
        elif intent.intent_type == AssistantIntentType.OPEN_APP:
            resolution = self.app_resolver.resolve(intent.target)
            if resolution.is_resolved:
                action = OpenAppAction(resolution=resolution, resolver=self.app_resolver)
                if auto_execute_actions:
                    act_res = action.execute()
                    result = OrchestratorResult(
                        text=act_res.message,
                        action=action,
                        metrics=ResponseMetrics(
                            total_duration_ms=(time.perf_counter() - t0) * 1000.0
                        ),
                        context=context,
                    )
                else:
                    result = OrchestratorResult(
                        text=f"Ready to open {resolution.canonical_name}.",
                        action=action,
                        context=context,
                    )
            else:
                result = OrchestratorResult(
                    text=f"Application '{resolution.canonical_name}' is not installed on this system.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # R. Native Capability: Screenshot
        elif intent.intent_type == AssistantIntentType.SCREENSHOT:
            if not auto_execute_actions:
                result = OrchestratorResult(
                    text="Ready to capture screenshot.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                cap_res = self.capabilities.execute("desktop.screenshot")
                result = OrchestratorResult(
                    text=cap_res.message
                    or (
                        f"Captured screenshot to {cap_res.data.get('path')}"
                        if cap_res.success
                        else f"Failed to capture screenshot: {cap_res.error}"
                    ),
                    capability_result=cap_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # S. Native Capability: Volume Set
        elif intent.intent_type == AssistantIntentType.VOLUME_SET:
            if not auto_execute_actions:
                result = OrchestratorResult(
                    text="Ready to adjust volume.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                cap_res = self.capabilities.execute("desktop.volume.set", **intent.extra)
                result = OrchestratorResult(
                    text=cap_res.message
                    or (
                        "Volume updated."
                        if cap_res.success
                        else f"Failed to set volume: {cap_res.error}"
                    ),
                    capability_result=cap_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # T. Native Capability: Volume Get
        elif intent.intent_type == AssistantIntentType.VOLUME_GET:
            cap_res = self.capabilities.execute("desktop.volume.get")
            result = OrchestratorResult(
                text=cap_res.message
                or (
                    f"System volume is at {cap_res.data.get('level')}%."
                    if cap_res.success
                    else f"Failed to get volume: {cap_res.error}"
                ),
                capability_result=cap_res,
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # U. Native Capability: Media Control
        elif intent.intent_type == AssistantIntentType.MEDIA_CONTROL:
            if not auto_execute_actions:
                result = OrchestratorResult(
                    text="Ready to control media playback.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                cap_res = self.capabilities.execute("desktop.media.control", **intent.extra)
                result = OrchestratorResult(
                    text=cap_res.message
                    or (
                        "Media playback command sent."
                        if cap_res.success
                        else f"Media control failed: {cap_res.error}"
                    ),
                    capability_result=cap_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # V. Native Capability: YouTube Search
        elif intent.intent_type == AssistantIntentType.YOUTUBE_SEARCH:
            query = intent.extra.get("query", intent.target or "")
            if not auto_execute_actions:
                result = OrchestratorResult(
                    text=f"Ready to search YouTube for: {query}",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                cap_res = self.capabilities.execute("web.youtube.search", query=query)
                result = OrchestratorResult(
                    text=cap_res.message
                    or (
                        f"Searching YouTube for {query}."
                        if cap_res.success
                        else f"Could not search YouTube: {cap_res.error}"
                    ),
                    capability_result=cap_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # ── Step 2: Screen observation & Agent Planner capabilities ──────
        if result is None:
            # First: Screen observation check ("what's on my screen", etc.)
            import re

            if re.search(
                r"\b(?:what(?:'s|\s+is)\s+on\s+my\s+screen|read\s+(?:what(?:'s|\s+is)\s+on\s+my\s+screen|my\s+screen)|what\s+am\s+i\s+looking\s+at)\b",
                normalized_prompt.lower(),
            ):
                screenshot_res = self.capabilities.execute("desktop.screenshot")
                if not screenshot_res.success:
                    result = OrchestratorResult(
                        text=f"Could not inspect your screen: {screenshot_res.error or screenshot_res.message}",
                        capability_result=screenshot_res,
                        metrics=ResponseMetrics(
                            total_duration_ms=(time.perf_counter() - t0) * 1000.0
                        ),
                        context=context,
                    )
                else:
                    path = screenshot_res.data.get("path", "")
                    provider = getattr(self.router, "_provider", None) or getattr(
                        self.router, "provider", None
                    )
                    caps = (
                        provider.capabilities()
                        if provider and hasattr(provider, "capabilities")
                        else None
                    )
                    if caps and caps.vision:
                        from avi.providers.models import AgentRequest

                        req = AgentRequest(
                            prompt="Describe what is currently visible on the user's screen in a concise summary.",
                            system_prompt="You are a helpful desktop assistant.",
                            context=context,
                        )
                        vision_resp = provider.send(req)
                        result = OrchestratorResult(
                            text=vision_resp.text,
                            capability_result=screenshot_res,
                            metrics=ResponseMetrics(
                                total_duration_ms=(time.perf_counter() - t0) * 1000.0
                            ),
                            context=context,
                        )
                    else:
                        model_name = (
                            provider.get_model_name()
                            if provider and hasattr(provider, "get_model_name")
                            else "local"
                        )
                        result = OrchestratorResult(
                            text=(
                                f"I captured a screenshot of your screen, but the active AI model "
                                f"({model_name}) does not support visual reasoning yet. "
                                f"The screenshot was saved locally to {path}."
                            ),
                            capability_result=screenshot_res,
                            metrics=ResponseMetrics(
                                total_duration_ms=(time.perf_counter() - t0) * 1000.0
                            ),
                            context=context,
                        )

            # Second: Agent capability planner
            if result is None:
                # Handle pending confirmation response
                if (
                    normalized_prompt.lower() in ("yes", "y", "confirm", "proceed", "do it")
                    and self.history.last_turn
                    and self.history.last_turn.plan
                    and getattr(self.history.last_turn.plan, "requires_confirmation", False)
                ):
                    pending_plan = self.history.last_turn.plan
                    plan_res = self.executor.execute_plan(pending_plan, confirmed=True)
                    result = OrchestratorResult(
                        text=plan_res.final_message,
                        plan=pending_plan,
                        capability_result=(
                            plan_res.completed_steps[-1].result
                            if plan_res.completed_steps
                            else None
                        ),
                        metrics=ResponseMetrics(
                            total_duration_ms=(time.perf_counter() - t0) * 1000.0
                        ),
                        context=context,
                    )
                else:
                    plan = self.planner.create_plan(normalized_prompt)
                    if plan is not None:
                        if plan.requires_confirmation:
                            result = OrchestratorResult(
                                text=plan.confirmation_prompt,
                                plan=plan,
                                requires_confirmation=True,
                                metrics=ResponseMetrics(
                                    total_duration_ms=(time.perf_counter() - t0) * 1000.0
                                ),
                                context=context,
                            )
                        elif not auto_execute_actions:
                            result = OrchestratorResult(
                                text=f"Ready to execute plan: {plan.user_goal}",
                                plan=plan,
                                context=context,
                            )
                        else:
                            plan_res = self.executor.execute_plan(plan, confirmed=False)
                            last_cap_res = (
                                plan_res.completed_steps[-1].result
                                if plan_res.completed_steps
                                else None
                            )
                            result = OrchestratorResult(
                                text=plan_res.final_message,
                                plan=plan,
                                requires_confirmation=plan_res.confirmation_required,
                                capability_result=last_cap_res,
                                metrics=ResponseMetrics(
                                    total_duration_ms=(time.perf_counter() - t0) * 1000.0
                                ),
                                context=context,
                            )

        # ── Step 2.5: Desktop domain boundary ────────────────────────────
        # Prevent unresolved desktop requests from falling through to LLM shell generator
        if result is None:
            desktop_keywords = {
                "volume",
                "volme",
                "vol",
                "sound",
                "audio",
                "screenshot",
                "screnshot",
                "screeshot",
                "mute",
                "unmute",
                "youtube",
                "youtub",
                "yotube",
            }
            lower_tokens = set(re.findall(r"\b\w+\b", normalized_prompt.lower()))
            if lower_tokens.intersection(desktop_keywords):
                if lower_tokens.intersection({"youtube", "youtub", "yotube"}):
                    msg = "What would you like me to search for on YouTube?"
                else:
                    msg = (
                        "Could you clarify your desktop request? For volume, you can say "
                        "'increase volume', 'decrease volume', 'mute', or 'set volume to 50%'. "
                        "For screenshots, say 'take a screenshot'."
                    )
                result = OrchestratorResult(
                    text=msg,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # ── Step 3: Fallback to Router (Deterministic Fast-Path, Tools, LLM) ──
        if result is None:
            resp = self.router.route_full(prompt, context=context)

            # Check if the router returned a proposed shell command
            proposal = self.router.parse_command_proposal(resp.text)
            if proposal is not None:
                assessment = self.safety.evaluate(proposal)
                if assessment.is_blocked:
                    result = OrchestratorResult(
                        text=f"Command:\n{proposal.command_line}\n\n[Blocked: {assessment.reason}]",
                        command_request=proposal,
                        safety_assessment=assessment,
                        is_blocked=True,
                        metrics=resp.metrics,
                        context=resp.context,
                    )
                elif assessment.requires_confirmation:
                    result = OrchestratorResult(
                        text=assessment.format_confirmation_prompt(),
                        command_request=proposal,
                        safety_assessment=assessment,
                        requires_confirmation=True,
                        metrics=resp.metrics,
                        context=resp.context,
                    )
                else:
                    # If SAFE, execute automatically
                    res = self.router.execute_command(proposal)
                    result = OrchestratorResult(
                        text=res.format_display(),
                        command_request=proposal,
                        execution_result=res,
                        safety_assessment=assessment,
                        metrics=resp.metrics,
                        context=resp.context,
                    )
            else:
                result = OrchestratorResult(
                    text=resp.text,
                    metrics=resp.metrics,
                    context=resp.context,
                )

        # Record turn in conversation history before returning
        self.history.add_turn(
            user_query=normalized_prompt,
            intent_type=intent.intent_type.value,
            response_text=result.text,
            tool_result=result.tool_result,
            action=result.action,
            command_request=result.command_request,
            execution_result=result.execution_result,
            plan=result.plan,
            capability_result=result.capability_result,
            target=intent.target,
        )
        return result
