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
from typing import Any, Iterator

from avi.actions.base import BaseAction
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
    format_system_info_conversational,
    get_memory_summary_conversational,
)
from avi.config import Config
from avi.core.router import Router
from avi.execution.models import CommandRequest, ExecutionResult
from avi.orchestrator.models import OrchestratorResult
from avi.providers.models import ResponseMetrics
from avi.safety.engine import SafetyEngine
from avi.safety.models import ActionCategory, RiskLevel, SafetyAssessment
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
    ) -> None:
        self.config = config
        self.tools = tools or create_default_registry()
        self.safety = safety or SafetyEngine()
        self.app_resolver = app_resolver or ApplicationResolver()
        self.router = router or Router(
            config=config,
            tools=self.tools,
            safety=self.safety,
        )

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
        intent = detect_assistant_intent(normalized_prompt)

        # A. Conversational greetings
        if intent.intent_type == AssistantIntentType.GREETING:
            return OrchestratorResult(
                text="Hello! How can I help?",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # B. Capabilities and help
        if intent.intent_type == AssistantIntentType.CAPABILITIES:
            return OrchestratorResult(
                text=(
                    "I can help with your computer, including system information, applications, "
                    "files, timers, and other tasks. I can also use an AI model when a request "
                    "needs reasoning or conversation."
                ),
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # C. Conversational small talk
        if intent.intent_type == AssistantIntentType.SMALL_TALK:
            return OrchestratorResult(
                text="I'm ready! Ask me about your system, open apps, set a timer, or ask a question.",
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # D. Native Tool: Disk Space
        if intent.intent_type == AssistantIntentType.DISK_SPACE:
            tool = self.tools.get("system.disk_usage")
            if tool:
                tool_res = tool.execute(path="/")
                text = format_disk_space_conversational(tool_res)
                return OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # E. Conversational Memory Overview
        if intent.intent_type == AssistantIntentType.MEMORY_TOTAL:
            text = get_memory_summary_conversational()
            return OrchestratorResult(
                text=text,
                metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                context=context,
            )

        # F. Native Tool: Memory / RAM Process Usage
        if intent.intent_type == AssistantIntentType.RAM_USAGE:
            tool = self.tools.get("system.processes")
            if tool:
                tool_res = tool.execute(limit=5, sort_by="memory")
                text = format_processes_conversational(tool_res, sort_by="memory")
                return OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # F. Native Tool: CPU
        if intent.intent_type == AssistantIntentType.CPU_USAGE:
            tool = self.tools.get("system.processes")
            if tool:
                tool_res = tool.execute(limit=5, sort_by="cpu")
                text = format_processes_conversational(tool_res, sort_by="cpu")
                return OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # G. Native Tool: Running processes list
        if intent.intent_type == AssistantIntentType.PROCESSES:
            tool = self.tools.get("system.processes")
            if tool:
                tool_res = tool.execute(limit=10, sort_by="memory")
                return OrchestratorResult(
                    text=tool_res.format_display(),
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # H. Native Tool: System info
        if intent.intent_type == AssistantIntentType.SYSTEM_INFO:
            tool = self.tools.get("system.system_info")
            if tool:
                tool_res = tool.execute()
                text = format_system_info_conversational(tool_res)
                return OrchestratorResult(
                    text=text,
                    tool_result=tool_res,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # I. Native Action: Timer
        if intent.intent_type == AssistantIntentType.TIMER:
            dur = intent.extra.get("duration_seconds", 0.0)
            label = intent.extra.get("label", "")
            action = TimerAction(duration_seconds=dur, label=label)
            if auto_execute_actions:
                start_msg = action.start_message
                act_res = action.execute()
                combined = f"{start_msg}\n{act_res.message}" if start_msg else act_res.message
                return OrchestratorResult(
                    text=combined,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            return OrchestratorResult(
                text=action.start_message,
                action=action,
                context=context,
            )

        # J. Native Action: Open URL
        if intent.intent_type == AssistantIntentType.OPEN_URL:
            action = OpenUrlAction(url=intent.target)
            if auto_execute_actions:
                act_res = action.execute()
                return OrchestratorResult(
                    text=act_res.message,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            return OrchestratorResult(
                text=f"Ready to open URL: {intent.target}",
                action=action,
                context=context,
            )

        # K. Native Action: Open File
        if intent.intent_type == AssistantIntentType.OPEN_FILE:
            action = OpenFileAction(path=intent.target)
            if auto_execute_actions:
                act_res = action.execute()
                return OrchestratorResult(
                    text=act_res.message,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            return OrchestratorResult(
                text=f"Ready to open file: {intent.target}",
                action=action,
                context=context,
            )

        # L. Native Action: Open Directory
        if intent.intent_type == AssistantIntentType.OPEN_DIR:
            action = OpenDirAction(path=intent.target)
            if auto_execute_actions:
                act_res = action.execute()
                return OrchestratorResult(
                    text=act_res.message,
                    action=action,
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )
            return OrchestratorResult(
                text=f"Ready to open directory: {intent.target}",
                action=action,
                context=context,
            )

        # M. Native Action: Open Application
        if intent.intent_type == AssistantIntentType.OPEN_APP:
            resolution = self.app_resolver.resolve(intent.target)
            if resolution.is_resolved:
                action = OpenAppAction(resolution=resolution, resolver=self.app_resolver)
                if auto_execute_actions:
                    act_res = action.execute()
                    return OrchestratorResult(
                        text=act_res.message,
                        action=action,
                        metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                        context=context,
                    )
                return OrchestratorResult(
                    text=f"Ready to open {resolution.canonical_name}.",
                    action=action,
                    context=context,
                )
            else:
                return OrchestratorResult(
                    text=f"Application '{resolution.canonical_name}' is not installed on this system.",
                    metrics=ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0),
                    context=context,
                )

        # ── Step 2: Fallback to Router (Deterministic Fast-Path, Tools, LLM) ──
        resp = self.router.route_full(prompt, context=context)

        # Check if the router returned a proposed shell command
        proposal = self.router.parse_command_proposal(resp.text)
        if proposal is not None:
            assessment = self.safety.evaluate(proposal)
            if assessment.is_blocked:
                return OrchestratorResult(
                    text=f"Command:\n{proposal.command_line}\n\n[Blocked: {assessment.reason}]",
                    command_request=proposal,
                    safety_assessment=assessment,
                    is_blocked=True,
                    metrics=resp.metrics,
                    context=resp.context,
                )
            if assessment.requires_confirmation:
                return OrchestratorResult(
                    text=assessment.format_confirmation_prompt(),
                    command_request=proposal,
                    safety_assessment=assessment,
                    requires_confirmation=True,
                    metrics=resp.metrics,
                    context=resp.context,
                )

            # If SAFE, execute automatically
            res = self.router.execute_command(proposal)
            return OrchestratorResult(
                text=res.format_display(),
                command_request=proposal,
                execution_result=res,
                safety_assessment=assessment,
                metrics=resp.metrics,
                context=resp.context,
            )

        return OrchestratorResult(
            text=resp.text,
            metrics=resp.metrics,
            context=resp.context,
        )
