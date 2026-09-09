"""Assistant Orchestrator subsystem for AVI.

Central coordination layer for:
- User intent recognition (greetings, system info, timers, apps, URLs)
- Tool-first selection and natural-language synthesis
- Native assistant action dispatch (Application launching, URLs, timers)
- FastPath deterministic matching
- Provider-agnostic AI reasoning and tool-selection
- Safe shell execution fallback through SafetyEngine
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

from avi.actions.system import OpenAppAction, OpenDirAction, OpenFileAction, OpenUrlAction
from avi.actions.timer import TimerAction
from avi.apps.resolver import ApplicationResolver
from avi.assistant.intents import (
    AssistantIntentType,
    classify_confirmation,
    clean_natural_language_input,
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
from avi.memory.manager import MemoryManager
from avi.orchestrator.models import ConversationHistory, OrchestratorResult, PendingClarification
from avi.providers.models import ProviderCapabilities, ResponseMetrics
from avi.providers.registry import select_provider
from avi.retrieval.youtube import validate_youtube_url
from avi.safety.engine import SafetyEngine
from avi.storage.database import Database
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
        database: Database | None = None,
        memory: MemoryManager | None = None,
        loop_guard: Any | None = None,
        agent_orchestrator: Any | None = None,
    ) -> None:
        self.config = config
        self.tools = tools or create_default_registry()
        self.safety = safety or SafetyEngine()
        self.app_resolver = app_resolver or ApplicationResolver()
        self.history = history or ConversationHistory()
        self.db = database or Database()
        self.memory = memory or MemoryManager(database=self.db)
        self.router = router or Router(
            config=config,
            tools=self.tools,
            safety=self.safety,
        )
        from avi.agent import AgentExecutor, AgentOrchestrator, AgentPlanner, LoopGuard
        from avi.capabilities import CapabilityRegistry, create_default_capability_registry

        self.capabilities: CapabilityRegistry = capabilities or create_default_capability_registry(
            tools=self.tools,
            resolver=self.app_resolver,
        )
        self.loop_guard: LoopGuard = loop_guard or LoopGuard()
        self.planner: AgentPlanner = planner or AgentPlanner()
        self.executor: AgentExecutor = executor or AgentExecutor(
            registry=self.capabilities,
            safety_engine=self.safety,
            database=self.db,
            loop_guard=self.loop_guard,
        )
        self.agent_orchestrator: AgentOrchestrator = agent_orchestrator or AgentOrchestrator(
            registry=self.capabilities,
            safety_engine=self.safety,
            database=self.db,
            loop_guard=self.loop_guard,
            planner=self.planner,
            executor=self.executor,
        )
        self.pending_clarification: PendingClarification | None = None

    def get_pending_clarification(self) -> PendingClarification | None:
        """Retrieve active pending clarification, checking in-memory state and persistent history."""
        if self.pending_clarification is not None:
            if self.pending_clarification.is_expired():
                self.pending_clarification = None
                return None
            return self.pending_clarification
        last = self.history.last_turn
        if last and getattr(last, "pending_clarification", None) is not None:
            p = last.pending_clarification
            if p.is_expired():
                last.pending_clarification = None
                return None
            return p
        return None

    def is_assistant_request(self, prompt: str) -> bool:
        """Determine if a prompt should be routed to native assistant capabilities rather than shell fallback."""
        normalized_prompt = clean_natural_language_input(prompt)
        if not normalized_prompt:
            return False

        # 1. Native assistant intent recognition (greetings, courtesies, tools, actions, volume, screenshot)
        intent = detect_assistant_intent(normalized_prompt, last_turn=self.history.last_turn)
        if intent.intent_type != AssistantIntentType.UNKNOWN:
            return True

        # 2. Pending clarification confirmation / cancellation check
        if (
            classify_confirmation(normalized_prompt) is not None
            and self.get_pending_clarification() is not None
        ):
            return True

        # 3. Screen observation intent
        import re

        if re.search(
            r"\b(?:what(?:'s|\s+is)\s+on\s+my\s+screen|read\s+(?:what(?:'s|\s+is)\s+on\s+my\s+screen|my\s+screen)|what\s+am\s+i\s+looking\s+at)\b",
            normalized_prompt.lower(),
        ):
            return True

        # 4. Pending confirmation for multi-step plan
        if (
            classify_confirmation(normalized_prompt) is not None
            and self.history.last_turn
            and self.history.last_turn.plan
            and getattr(self.history.last_turn.plan, "requires_confirmation", False)
        ):
            return True

        # 4. Capability agent planner (composite or single-step plans)
        if self.planner.create_plan(normalized_prompt) is not None:
            return True

        if hasattr(self, "agent_orchestrator") and self.agent_orchestrator.can_handle(normalized_prompt):
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
            "screeenshot",
            "mute",
            "unmute",
            "youtube",
            "youtub",
            "yotube",
            "activaite",
            "activte",
            "actvate",
            "video",
            "vid",
            "vids",
            "videos",
            "play",
            "media",
            "music",
            "song",
            "track",
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
        confirmed: bool = False,
    ) -> OrchestratorResult:
        """Process user input through the assistant hierarchy."""
        t0 = time.perf_counter()
        normalized_prompt = clean_natural_language_input(prompt)
        if not normalized_prompt:
            return OrchestratorResult(text="")

        # ── Check active pending clarification before standard dispatch ────────
        pending = self.get_pending_clarification()
        if pending is not None:
            conf = classify_confirmation(normalized_prompt)
            if conf is True:
                target_cmd = pending.proposed_interpretation
                self.pending_clarification = None
                if self.history.last_turn:
                    self.history.last_turn.pending_clarification = None
                try:
                    from avi.session.state import clear_session_state

                    clear_session_state()
                except Exception:
                    pass

                logger.info(
                    "Clarification confirmed: '%s' -> executing '%s'",
                    pending.original_prompt,
                    target_cmd,
                )
                return self.handle(
                    target_cmd,
                    context=context,
                    auto_execute_actions=auto_execute_actions,
                )
            elif conf is False:
                self.pending_clarification = None
                if self.history.last_turn:
                    self.history.last_turn.pending_clarification = None
                try:
                    from avi.session.state import clear_session_state

                    clear_session_state()
                except Exception:
                    pass

                logger.info(
                    "Clarification rejected: '%s' cancelled by '%s'",
                    pending.original_prompt,
                    normalized_prompt,
                )
                result = OrchestratorResult(
                    text="Okay, cancelled.",
                    metrics=ResponseMetrics(
                        total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                        routing_duration_ms=(time.perf_counter() - t0) * 1000.0,
                    ),
                    context=context,
                )
                self.history.add_turn(
                    user_query=normalized_prompt,
                    intent_type="CANCEL",
                    response_text=result.text,
                )
                return result
            else:
                # User provided an unrelated command: clear pending clarification and continue normal dispatch
                self.pending_clarification = None
                if self.history.last_turn:
                    self.history.last_turn.pending_clarification = None
                try:
                    from avi.session.state import clear_session_state

                    clear_session_state()
                except Exception:
                    pass

        # ── Step 1: Detect native assistant intent ───────────────────────
        intent = detect_assistant_intent(normalized_prompt, last_turn=self.history.last_turn)
        routing_duration_ms = (time.perf_counter() - t0) * 1000.0
        result: OrchestratorResult | None = None

        # A. Conversational greetings
        if intent.intent_type == AssistantIntentType.GREETING:
            result = OrchestratorResult(
                text="Hello! How can I help?",
                metrics=ResponseMetrics(
                    total_duration_ms=routing_duration_ms,
                    routing_duration_ms=routing_duration_ms,
                ),
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
            suggested = intent.target or intent.extra.get("suggested", "")
            if suggested:
                self.pending_clarification = PendingClarification(
                    original_prompt=normalized_prompt,
                    proposed_interpretation=suggested,
                    clarification_type=intent.extra.get("clarification_type", "typo"),
                    created_at=time.time(),
                    metadata=dict(intent.extra),
                )
            result = OrchestratorResult(
                text=msg,
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
                pending_clarification=self.pending_clarification,
            )

        # E1. Confirmation of pending clarification or plan
        elif intent.intent_type == AssistantIntentType.CONFIRMATION:
            confirmed_target = intent.target or intent.extra.get("confirmed_target")
            action = intent.extra.get("action")
            if action == "confirm_plan" and self.history.last_turn and self.history.last_turn.plan:
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
                        total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                        planning_duration_ms=plan_res.planning_duration_ms,
                        action_duration_ms=plan_res.action_duration_ms,
                        verification_duration_ms=plan_res.verification_duration_ms,
                    ),
                    context=context,
                )
            elif confirmed_target:
                self.pending_clarification = None
                if self.history.last_turn:
                    self.history.last_turn.pending_clarification = None
                try:
                    from avi.session.state import clear_session_state

                    clear_session_state()
                except Exception:
                    pass
                return self.handle(
                    confirmed_target,
                    context=context,
                    auto_execute_actions=auto_execute_actions,
                )
            else:
                result = OrchestratorResult(
                    text="Confirmed.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # E2. Cancellation
        elif intent.intent_type == AssistantIntentType.CANCELLATION:
            self.pending_clarification = None
            if self.history.last_turn:
                self.history.last_turn.pending_clarification = None
            try:
                from avi.session.state import clear_session_state

                clear_session_state()
            except Exception:
                pass
            result = OrchestratorResult(
                text="Okay, cancelled.",
                metrics=ResponseMetrics(
                    total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                    routing_duration_ms=routing_duration_ms,
                ),
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

        # H2. Conversational / Explicit Memory Command
        elif intent.intent_type == AssistantIntentType.MEMORY:
            t_mem = time.perf_counter()
            handled, mem_text = self.memory.handle_memory_command(normalized_prompt)
            mem_dur = (time.perf_counter() - t_mem) * 1000.0
            result = OrchestratorResult(
                text=mem_text if handled else "I couldn't process that memory request.",
                metrics=ResponseMetrics(
                    total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                    routing_duration_ms=routing_duration_ms,
                    memory_duration_ms=mem_dur,
                ),
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
            browser = intent.extra.get("browser")
            if not browser:
                browser = self.memory.get_preferred_browser()
            action = OpenUrlAction(url=intent.target, browser=browser)
            if auto_execute_actions:
                act_res = action.execute()
                result = OrchestratorResult(
                    text=act_res.message,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
                browser_str = f" in {browser.title()}" if browser else ""
                result = OrchestratorResult(
                    text=f"Ready to open URL: {intent.target}{browser_str}",
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
            from pathlib import Path
            target_path = intent.target
            p = Path(target_path).expanduser()
            if not p.exists():
                pref = self.memory.get_preferred_folder(target_path)
                if pref:
                    target_path = pref
            action = OpenDirAction(path=target_path)
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
                    text=f"Ready to open directory: {target_path}",
                    action=action,
                    context=context,
                )

        # Q. Native Action: Open Application
        elif intent.intent_type == AssistantIntentType.OPEN_APP:
            matches = self.app_resolver.find_matching_applications(intent.target)
            if len(matches) > 1 and intent.target.lower() not in self.app_resolver.aliases:
                app_names = ", ".join(m.canonical_name for m in matches)
                result = OrchestratorResult(
                    text=f"I found multiple matching applications: {app_names}. Which one would you like to open?",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            else:
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
                        text=f"I couldn't find {resolution.canonical_name} installed. Application '{resolution.canonical_name}' is not installed on this system.",
                        metrics=ResponseMetrics(
                            total_duration_ms=(time.perf_counter() - t0) * 1000.0
                        ),
                        context=context,
                    )

        # Q2. Native Assistant Activation
        elif intent.intent_type == AssistantIntentType.ACTIVATE:
            result = OrchestratorResult(
                text="Activating AVI...\nAVI is active and ready. How can I help you?",
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

        # W. Native Capability: YouTube Smart Recommend
        elif intent.intent_type == AssistantIntentType.YOUTUBE_RECOMMEND:
            result = self._handle_youtube_recommend(intent, context, t0, auto_execute_actions)

        # X. Native Capability: Open Previous Search Result
        elif intent.intent_type == AssistantIntentType.OPEN_SEARCH_RESULT:
            result = self._handle_open_search_result(intent, context, t0, auto_execute_actions)

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
                has_pending_plan = (
                    self.history.last_turn
                    and self.history.last_turn.plan
                    and getattr(self.history.last_turn.plan, "requires_confirmation", False)
                )
                if has_pending_plan and classify_confirmation(normalized_prompt) is True:
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
                            total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                            planning_duration_ms=plan_res.planning_duration_ms,
                            action_duration_ms=plan_res.action_duration_ms,
                            verification_duration_ms=plan_res.verification_duration_ms,
                        ),
                        context=context,
                    )
                elif has_pending_plan and classify_confirmation(normalized_prompt) is False:
                    result = OrchestratorResult(
                        text="Operation cancelled.",
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
                                    total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                                    planning_duration_ms=plan_res.planning_duration_ms,
                                    action_duration_ms=plan_res.action_duration_ms,
                                    verification_duration_ms=plan_res.verification_duration_ms,
                                ),
                                context=context,
                            )
                    else:
                        agent_ctx = self.agent_orchestrator.run(normalized_prompt, confirmed=confirmed)
                        if agent_ctx.steps or (agent_ctx.final_response and agent_ctx.final_response != "I couldn't find a matching action or plan for that request."):
                            from avi.agent.context import TaskStatus
                            req_confirm = agent_ctx.status == TaskStatus.PAUSED_FOR_CONFIRMATION
                            result = OrchestratorResult(
                                text=agent_ctx.final_response,
                                requires_confirmation=req_confirm,
                                metrics=ResponseMetrics(
                                    total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                                ),
                                context=context,
                            )

        # ── Step 2.5: Desktop and application domain boundary ───────────
        # Prevent unresolved desktop requests or application queries from falling through to LLM shell generator
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
                "screeenshot",
                "mute",
                "unmute",
                "youtube",
                "youtub",
                "yotube",
                "activaite",
                "activte",
                "actvate",
                "video",
                "vid",
                "vids",
                "videos",
                "play",
                "media",
                "music",
                "song",
                "track",
            }
            lower_tokens = set(re.findall(r"\b\w+\b", normalized_prompt.lower()))
            if lower_tokens.intersection(desktop_keywords):
                if lower_tokens.intersection({"activate", "activaite", "activte", "actvate"}):
                    msg = "Did you mean activate AVI?"
                elif lower_tokens.intersection({"youtube", "youtub", "yotube"}):
                    msg = "What would you like me to search for on YouTube?"
                elif lower_tokens.intersection({"video", "vid", "vids", "videos", "play"}):
                    msg = "What video would you like me to play?"
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
            t_route = time.perf_counter()
            resp = self.router.route_full(prompt, context=context)
            llm_duration_ms = (time.perf_counter() - t_route) * 1000.0
            if resp.metrics is not None and resp.metrics.llm_duration_ms is None:
                resp.metrics.llm_duration_ms = llm_duration_ms

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
            search_results=result.search_results,
            selected_result=result.selected_result,
            pending_clarification=self.pending_clarification,
        )

        # Developer/debug timing instrumentation
        total_ms = (time.perf_counter() - t0) * 1000.0
        if result.metrics:
            result.metrics.total_duration_ms = total_ms
            if result.metrics.routing_duration_ms is None:
                result.metrics.routing_duration_ms = routing_duration_ms
        else:
            result.metrics = ResponseMetrics(
                total_duration_ms=total_ms,
                routing_duration_ms=routing_duration_ms,
            )

        if intent.intent_type == AssistantIntentType.GREETING:
            logger.debug("[AVI] route=greeting routing=%.1fms total=%.1fms", routing_duration_ms, total_ms)
        elif result.metrics and result.metrics.llm_duration_ms is not None:
            logger.debug("[AVI] route=llm llm=%.1fms total=%.1fms", result.metrics.llm_duration_ms, total_ms)
        elif intent.intent_type != AssistantIntentType.UNKNOWN:
            route_name = intent.intent_type.value.lower()
            cap_ms = getattr(result.metrics, "capability_duration_ms", None)
            if cap_ms is not None:
                logger.debug(
                    "[AVI] route=%s capability=%.1fms total=%.1fms",
                    route_name,
                    cap_ms,
                    total_ms,
                )
            else:
                logger.debug(
                    "[AVI] route=%s routing=%.1fms total=%.1fms",
                    route_name,
                    routing_duration_ms,
                    total_ms,
                )
        else:
            llm_ms = getattr(result.metrics, "eval_duration_ms", None) or (total_ms - routing_duration_ms)
            logger.debug("[AVI] route=llm llm=%.1fms total=%.1fms", llm_ms, total_ms)

        return result

    # ------------------------------------------------------------------
    # Private helpers: smart YouTube recommend + open search result
    # ------------------------------------------------------------------

    _MAX_RETRIEVAL_RESULTS = 10
    _MAX_REASONING_RESULTS = 5

    def _handle_youtube_recommend(
        self,
        intent: Any,
        context: Any,
        t0: float,
        auto_execute_actions: bool,
    ) -> OrchestratorResult:
        """Retrieve YouTube results and optionally rank with a provider."""
        import json
        import logging
        import os

        log = logging.getLogger("avi.orchestrator.recommend")

        query: str = intent.extra.get("query", intent.target or "")
        from_history: bool = bool(intent.extra.get("from_history", False))
        constraints: dict = intent.extra.get("constraints", {})

        log.debug("youtube.intent.detected query=%s constraints=%s", query, constraints)

        # If re-ranking from previous search, reuse stored results
        raw_results: list | None = None
        retrieval_duration_ms: float | None = None
        if from_history:
            last_turn = self.history.last_turn
            if last_turn and last_turn.search_results:
                raw_results = last_turn.search_results
            else:
                return OrchestratorResult(
                    text="I don't have any previous search results to rank. What would you like me to search for?",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        if raw_results is None:
            if not query:
                return OrchestratorResult(
                    text="What topic would you like me to search YouTube for?",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

            log.debug("youtube.provider.selected provider=youtube_innertube")
            log.debug(
                "youtube.request.started endpoint=https://www.youtube.com/youtubei/v1/search query=%s",
                query,
            )
            t_retrieval_start = time.perf_counter()
            cap_res = self.capabilities.execute(
                "web.youtube.search_results",
                query=query,
                limit=self._MAX_RETRIEVAL_RESULTS,
            )
            retrieval_duration_ms = (time.perf_counter() - t_retrieval_start) * 1000.0
            log.debug(
                "youtube.request.completed count=%d duration_ms=%.1f",
                len(cap_res.data.get("search_results", [])) if cap_res.success else 0,
                retrieval_duration_ms,
            )

            if not cap_res.success:
                err_msg = str(cap_res.error or "")
                if cap_res.error == "YouTube search timed out. Try again.":
                    user_msg = "YouTube search timed out. Try again."
                elif cap_res.error == "I couldn't reach YouTube right now.":
                    user_msg = "I couldn't reach YouTube right now."
                elif "network" in err_msg.lower():
                    user_msg = (
                        cap_res.message
                        or "I couldn't reach YouTube right now. Network unavailable."
                    )
                elif "timed out" in err_msg.lower():
                    user_msg = "YouTube search timed out. Try again."
                else:
                    user_msg = (
                        cap_res.message or f"Could not retrieve YouTube results: {cap_res.error}"
                    )
                return OrchestratorResult(
                    text=user_msg,
                    capability_result=cap_res,
                    metrics=ResponseMetrics(
                        total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                        retrieval_duration_ms=retrieval_duration_ms,
                    ),
                    context=context,
                )
            raw_results = cap_res.data.get("search_results", [])

        if not raw_results:
            return OrchestratorResult(
                text="I couldn't find any matching YouTube videos.",
                metrics=ResponseMetrics(
                    total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                    retrieval_duration_ms=retrieval_duration_ms,
                ),
                context=context,
            )

        # Apply duration constraint if present
        min_secs = constraints.get("min_duration_seconds")
        max_secs = constraints.get("max_duration_seconds")
        filtered = raw_results
        if min_secs is not None or max_secs is not None:
            filtered = [
                r
                for r in raw_results
                if (
                    r.metadata.get("duration_seconds") is not None
                    and (min_secs is None or r.metadata["duration_seconds"] >= min_secs)
                    and (max_secs is None or r.metadata["duration_seconds"] <= max_secs)
                )
            ] or raw_results  # fall back to unfiltered if nothing passes

        # Truncate to reasoning window
        reasoning_candidates = filtered[: self._MAX_REASONING_RESULTS]
        log.debug("youtube.results.parsed count=%d", len(reasoning_candidates))

        # Build result ID → result mapping for validation
        result_map = {r.id: r for r in reasoning_candidates}

        selected_result = None
        reason_text = ""

        # Try provider-based ranking
        reasoning_provider = select_provider(
            required=ProviderCapabilities(text_reasoning=True, structured_output=True),
            active_provider=self.router.provider,
        )

        # Detect if the active provider is a local Ollama model (slow CPU inference)
        is_ollama = (
            reasoning_provider is not None
            and reasoning_provider.__class__.__name__ == "OllamaProvider"
        )

        enable_llm_ranking = bool(
            os.environ.get("AVI_LLM_YOUTUBE_RANKING", "").lower() in ("1", "true", "yes")
            or getattr(self.config, "enable_llm_ranking", False)
        )

        if is_ollama and not enable_llm_ranking:
            # Deterministic first-class candidate selection (<1ms) without blocking Ollama on CPU
            selected_result = reasoning_candidates[0] if reasoning_candidates else None
            if selected_result:
                if selected_result.channel:
                    reason_text = f"Top result on YouTube by {selected_result.channel}."
                else:
                    reason_text = "Top relevant result matching your request."
        elif reasoning_provider is not None and auto_execute_actions and reasoning_candidates:
            candidates_json = json.dumps(
                [
                    {
                        "id": r.id,
                        "title": r.title,
                        "channel": r.channel or "Unknown",
                        "duration": r.duration or "?",
                        "description": (r.description or "")[:200],
                    }
                    for r in reasoning_candidates
                ],
                ensure_ascii=False,
            )
            user_query_hint = (
                f'User request: "{query}"' if query else "User wants a recommendation."
            )
            prompt = (
                f"{user_query_hint}\n\n"
                f"Here are the top YouTube search results:\n{candidates_json}\n\n"
                "Select the single best result for the user. "
                'Respond with ONLY valid JSON: {"selected_result_id": "<id>", "reason": "<one sentence>"}\n'
                "Do not add any other text or explanation."
            )
            import concurrent.futures

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(reasoning_provider.generate_full, prompt=prompt)
                    resp = future.result(timeout=3.0)
                    raw_text = (resp.text or "").strip()
                    import re

                    json_match = re.search(r"\{[^{}]+\}", raw_text, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group())
                        sel_id = parsed.get("selected_result_id", "")
                        if sel_id in result_map:
                            selected_result = result_map[sel_id]
                            reason_text = parsed.get("reason", "")
            except Exception as exc:
                log.warning("Provider ranking failed or timed out: %s", exc)
                if is_ollama and reasoning_candidates:
                    selected_result = reasoning_candidates[0]
                    reason_text = "Top relevant result matching your request."

        if selected_result is not None:
            log.debug(
                "youtube.recommendation.ranked selected_id=%s reason=%s",
                selected_result.id,
                reason_text,
            )
            log.debug("youtube.session.saved count=%d", len(raw_results))

        # Format response
        if selected_result is not None:
            lines = [
                f'I found a few results for "{query}".'
                if query
                else "Based on the search results:",
                "",
                f"🎯 Best match: **{selected_result.title}**",
            ]
            if selected_result.channel:
                lines.append(f"   Channel: {selected_result.channel}")
            if selected_result.duration:
                lines.append(f"   Duration: {selected_result.duration}")
            if reason_text:
                lines.append(f"   Why: {reason_text}")
            lines.append("")
            lines.append('Say "open it" to watch, or ask me to open a specific result.')
            # Append brief list of all candidates
            if len(reasoning_candidates) > 1:
                lines.append("")
                lines.append("Other results:")
                for i, r in enumerate(reasoning_candidates, 1):
                    marker = "→" if r is selected_result else f"{i}."
                    lines.append(f"  {marker} {r.title}" + (f" ({r.channel})" if r.channel else ""))
            response_text = "\n".join(lines)
        else:
            # Just list results
            lines = [
                f'Here are YouTube results for "{query}":' if query else "Here are the results:",
                "",
            ]
            for i, r in enumerate(reasoning_candidates, 1):
                lines.append(f"{i}. {r.title}")
                if r.channel:
                    lines.append(f"   Channel: {r.channel}")
                if r.duration:
                    lines.append(f"   Duration: {r.duration}")
            lines.append("")
            lines.append('Say "open the first one" or "open it" to watch.')
            response_text = "\n".join(lines)

        return OrchestratorResult(
            text=response_text,
            search_results=raw_results,
            selected_result=selected_result,
            metrics=ResponseMetrics(
                total_duration_ms=(time.perf_counter() - t0) * 1000.0,
                retrieval_duration_ms=retrieval_duration_ms,
            ),
            context=context,
        )

    def _handle_open_search_result(
        self,
        intent: Any,
        context: Any,
        t0: float,
        auto_execute_actions: bool,
    ) -> OrchestratorResult:
        """Open a specific result from the previous YouTube search."""
        last_turn = self.history.last_turn
        search_results: list = getattr(last_turn, "search_results", None) or []
        selected_result = getattr(last_turn, "selected_result", None)
        index: int = intent.extra.get("index", 0)

        target_result = None

        if index == 0 and selected_result is not None:
            # "Open it" → open the previously recommended result
            target_result = selected_result
        elif search_results and 0 <= index < len(search_results):
            target_result = search_results[index]
        elif search_results:
            return OrchestratorResult(
                text=f"I only have {len(search_results)} result(s). "
                'Try "open the first one" or "open it".',
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )
        else:
            return OrchestratorResult(
                text="I don't have a recent result to open.",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # Validate URL before opening
        if not validate_youtube_url(target_result.url):
            return OrchestratorResult(
                text="That result has an invalid URL and cannot be opened safely.",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        if not auto_execute_actions:
            return OrchestratorResult(
                text=f"Ready to open: {target_result.title}",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        cap_res = self.capabilities.execute("desktop.url.open", url=target_result.url)
        if cap_res.success:
            text = f'Opening "{target_result.title}".'
        else:
            text = f"Could not open the video: {cap_res.error or cap_res.message}"

        return OrchestratorResult(
            text=text,
            capability_result=cap_res,
            search_results=search_results,
            selected_result=selected_result,
            action_url=target_result.url,
            metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
            context=context,
        )
