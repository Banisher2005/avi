"""Request routing subsystem for AVI."""

import re
import time
from typing import Any, Iterator

from avi.config import Config
from avi.context.collectors import (
    collect_git_context,
    collect_snapshot,
    collect_terminal_context,
    detect_needed_context,
)
from avi.context.models import ContextSnapshot
from avi.core.fastpath import FastPathRegistry, resolve_command_template
from avi.core.normalizer import normalize_response, normalize_stream
from avi.execution import (
    CommandExecutor,
    CommandRequest,
    ExecutionResult,
    extract_command_proposal,
)
from avi.providers.base import (
    AgentRequest,
    AgentResponse,
    BaseProvider,
    ProviderResponse,
    ResponseMetrics,
    ToolCall,
)
from avi.providers.registry import ProviderRegistry, get_default_registry
from avi.safety import RiskLevel, SafetyAssessment, SafetyEngine
from avi.tools.base import ToolResult
from avi.tools.registry import ToolRegistry, create_default_registry

# Regex patterns for deterministic fast-path context and tool queries
_FAST_PATH_CWD = re.compile(
    r"^(what\s+(is\s+my\s+|the\s+)?(current\s+)?directory(\s+(am\s+i\s+in|is\s+this))?|where\s+am\s+i|current\s+directory|pwd)$",
    re.IGNORECASE,
)
_FAST_PATH_BRANCH = re.compile(
    r"^(what\s+(is\s+my\s+|the\s+)?(git\s+)?branch(\s+(am\s+i\s+on|is\s+this))?|(show\s+(me\s+|my\s+)?(current\s+)?|current\s+)(git\s+)?branch|git\s+branch)$",
    re.IGNORECASE,
)
_FAST_PATH_SHELL = re.compile(
    r"^(what\s+(is\s+my\s+|the\s+)?shell(\s+(am\s+i\s+using|is\s+this))?|current\s+shell)$",
    re.IGNORECASE,
)
_FAST_PATH_OS = re.compile(
    r"^(what\s+(is\s+my\s+|the\s+)?(operating\s+system|os)(\s+(am\s+i\s+on|is\s+this|am\s+i\s+using))?|current\s+os|system\s+info)$",
    re.IGNORECASE,
)
_FAST_PATH_FILES = re.compile(
    r"^(what\s+files\s+(are\s+)?(here|in\s+this\s+directory)|list\s+files(\s+in\s+this\s+directory|\s+here)?|show\s+files(\s+here)?|ls)$",
    re.IGNORECASE,
)
_FAST_PATH_RAM = re.compile(
    r"^(what(\'s|\s+is)\s+using\s+(the\s+most\s+)?(ram|memory)|show\s+processes|top\s+processes|list\s+processes)$",
    re.IGNORECASE,
)
_FAST_PATH_DISK = re.compile(
    r"^(how\s+much\s+disk\s+space\s+(do\s+i\s+have|is\s+left)|disk\s+usage|check\s+disk\s+space|disk\s+space)$",
    re.IGNORECASE,
)
_FAST_PATH_GIT_STATUS = re.compile(
    r"^(git\s+status|is\s+(the\s+|my\s+)?repo\s+clean|check\s+git\s+status)$",
    re.IGNORECASE,
)
_FAST_PATH_GIT_LOG = re.compile(
    r"^(recent\s+commits|git\s+log|show\s+commits|git\s+history)$",
    re.IGNORECASE,
)


class Router:
    """Routes incoming user requests to tools, fast-path handlers, or model providers."""

    def __init__(
        self,
        config: Config,
        provider: BaseProvider | None = None,
        tools: ToolRegistry | None = None,
        safety: SafetyEngine | None = None,
        executor: CommandExecutor | None = None,
        fastpath: FastPathRegistry | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or get_default_registry()
        self._provider = provider or self._init_provider(config)
        self.tools = tools or create_default_registry()
        self.safety = safety or SafetyEngine()
        self.executor = executor or CommandExecutor(
            default_timeout=config.command_timeout,
            default_max_output_bytes=config.max_output_bytes,
        )
        self.fastpath = fastpath or FastPathRegistry()
        self._fast_path_metrics: ResponseMetrics | None = None

    def _init_provider(self, config: Config) -> BaseProvider:
        """Initialize provider based on configuration via ProviderRegistry."""
        return self.registry.get(config.provider, config=config)

    def execute_tool_call(self, call: ToolCall) -> ToolResult:
        """Resolve and execute an AI provider tool call through ToolRegistry.

        Safety Boundary Invariant:
        AI -> ToolCall -> AVI Tool Registry -> Safety -> Tool Execution -> ToolResult
        """
        tool = self.tools.get(call.name)
        if tool is None:
            available = ", ".join(sorted(self.tools.list_names()))
            return ToolResult(
                success=False,
                error=f"Unknown tool: '{call.name}'. Available tools: {available}",
            )
        try:
            return tool.execute(**call.arguments)
        except Exception as err:
            return ToolResult(success=False, error=str(err))

    @property
    def provider(self) -> BaseProvider:
        """The underlying model provider."""
        return self._provider

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        """Return metrics from the last request."""
        return self._fast_path_metrics or self._provider.last_metrics

    @property
    def last_context(self) -> Any | None:
        """Return conversation context token state from the last request."""
        return self._provider.last_context

    def warmup(self) -> bool:
        """Trigger backend warmup to ensure sub-second response times."""
        return self._provider.warmup()

    def check_fast_path(self, prompt: str) -> str | None:
        """Deterministic fast-path and read-only tool lookup without invoking an LLM.

        Resolves common environment and system inspection requests in < 10 ms.
        """
        normalized = prompt.strip().rstrip("?.!").strip()
        t0 = time.perf_counter()

        result = None

        # 1. Directory / CWD
        if _FAST_PATH_CWD.match(normalized):
            result = collect_terminal_context().cwd

        # 2. Git Branch
        elif _FAST_PATH_BRANCH.match(normalized):
            tool = self.tools.get("git.branch")
            if tool:
                result = tool.execute().format_display()
            else:
                git_ctx = collect_git_context()
                result = git_ctx.branch if git_ctx.is_repo else "Not in a Git repository."

        # 3. Shell
        elif _FAST_PATH_SHELL.match(normalized):
            result = collect_terminal_context().shell

        # 4. Operating System / System Info
        elif _FAST_PATH_OS.match(normalized):
            tool = self.tools.get("system.system_info")
            if tool and "system info" in normalized.lower():
                result = tool.execute().format_display()
            else:
                result = collect_terminal_context().os_name

        # 5. List Files
        elif _FAST_PATH_FILES.match(normalized):
            tool = self.tools.get("filesystem.list_directory")
            if tool:
                result = tool.execute().format_display()

        # 6. Memory / Processes
        elif _FAST_PATH_RAM.match(normalized):
            tool = self.tools.get("system.processes")
            if tool:
                result = tool.execute(limit=10, sort_by="memory").format_display()

        # 7. Disk Usage
        elif _FAST_PATH_DISK.match(normalized):
            tool = self.tools.get("system.disk_usage")
            if tool:
                result = tool.execute(path="/").format_display()

        # 8. Git Status
        elif _FAST_PATH_GIT_STATUS.match(normalized):
            tool = self.tools.get("git.status")
            if tool:
                result = tool.execute().format_display()

        # 9. Git Log
        elif _FAST_PATH_GIT_LOG.match(normalized):
            tool = self.tools.get("git.log")
            if tool:
                result = tool.execute(limit=5).format_display()

        if result is not None:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._fast_path_metrics = ResponseMetrics(total_duration_ms=elapsed_ms)
            return result

        self._fast_path_metrics = None
        return None

    def should_delegate(self, prompt: str) -> bool:
        """Check if request requires complex agent delegation (e.g. Antigravity).

        To be extended in Phase 7.
        """
        return False

    def assemble_system_prompt(self, prompt: str, explicit_snapshot: ContextSnapshot | None = None) -> str:
        """Assemble system prompt with lazy, minimal context injection."""
        base_prompt = self.config.system_prompt

        # Use explicit snapshot if provided, or lazily detect and collect needed context
        if explicit_snapshot is not None:
            context_block = explicit_snapshot.to_prompt_context()
        else:
            needed = detect_needed_context(prompt)
            if not needed:
                return base_prompt
            snapshot = collect_snapshot(needed)
            context_block = snapshot.to_prompt_context()

        if not context_block:
            return base_prompt

        return f"{base_prompt}\n\nContext:\n{context_block}"

    def route(
        self,
        prompt: str,
        context: Any | None = None,
        stream: bool = True,
        explicit_snapshot: ContextSnapshot | None = None,
    ) -> Iterator[str]:
        """Route request through fast-path, read-only tools, or LLM provider with streaming."""
        # 1. Check Fast-Path / Deterministic Tools
        fast_result = self.check_fast_path(prompt)
        if fast_result is not None:
            yield fast_result
            return

        # 2. Check Command Syntax Templates (Phase 6)
        cmd_syntax = resolve_command_template(prompt)
        if cmd_syntax is not None:
            t0 = time.perf_counter()
            self._fast_path_metrics = ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0)
            yield cmd_syntax
            return

        # 3. Check Deterministic Command-Template Fast-Path (Phase 6)
        fastpath_req = self.fastpath.resolve(prompt)
        if fastpath_req is not None:
            assessment = self.safety.evaluate(fastpath_req)
            if assessment.is_safe:
                t0 = time.perf_counter()
                res = self.executor.execute(fastpath_req)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._fast_path_metrics = ResponseMetrics(total_duration_ms=elapsed_ms)
                display = res.format_display()
                if display:
                    yield display
                return
            elif assessment.is_blocked:
                yield f"Command:\n{fastpath_req.command_line}\n\n[Blocked: {assessment.reason}]"
                return
            elif assessment.requires_confirmation:
                yield f"COMMAND: {fastpath_req.command_line}"
                return

        # 4. Lazy Context Assembly
        system_prompt = self.assemble_system_prompt(prompt, explicit_snapshot=explicit_snapshot)

        # 5. AI Provider Invocation via AgentRequest
        req = AgentRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
            stream=stream,
        )
        raw_stream = self._provider.stream(req)

        if stream:
            yield from normalize_stream(raw_stream)
        else:
            full_text = "".join(raw_stream)
            yield normalize_response(full_text)

    def route_full(
        self,
        prompt: str,
        context: Any | None = None,
        explicit_snapshot: ContextSnapshot | None = None,
    ) -> ProviderResponse:
        """Route request and return complete response with timing metrics and context."""
        fast_result = self.check_fast_path(prompt)
        if fast_result is not None:
            return ProviderResponse(
                text=fast_result,
                metrics=self._fast_path_metrics,
                context=context,
            )

        # 2. Check Command Syntax Templates (Phase 6)
        cmd_syntax = resolve_command_template(prompt)
        if cmd_syntax is not None:
            t0 = time.perf_counter()
            self._fast_path_metrics = ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0)
            return ProviderResponse(
                text=cmd_syntax,
                metrics=self._fast_path_metrics,
                context=context,
            )

        # 3. Check Deterministic Command-Template Fast-Path (Phase 6)
        fastpath_req = self.fastpath.resolve(prompt)
        if fastpath_req is not None:
            assessment = self.safety.evaluate(fastpath_req)
            if assessment.is_safe:
                t0 = time.perf_counter()
                res = self.executor.execute(fastpath_req)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._fast_path_metrics = ResponseMetrics(total_duration_ms=elapsed_ms)
                return ProviderResponse(
                    text=res.format_display(),
                    metrics=self._fast_path_metrics,
                    context=context,
                )
            elif assessment.is_blocked:
                return ProviderResponse(
                    text=f"Command:\n{fastpath_req.command_line}\n\n[Blocked: {assessment.reason}]",
                    context=context,
                )
            elif assessment.requires_confirmation:
                return ProviderResponse(
                    text=f"COMMAND: {fastpath_req.command_line}",
                    context=context,
                )

        # 4. Lazy Context Assembly
        system_prompt = self.assemble_system_prompt(prompt, explicit_snapshot=explicit_snapshot)

        # 5. AI Provider Invocation via AgentRequest
        req = AgentRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
            stream=False,
        )
        resp = self._provider.send(req)

        # If provider requested tool calls, resolve them through ToolRegistry
        if resp.tool_calls:
            results = []
            for tc in resp.tool_calls:
                res = self.execute_tool_call(tc)
                results.append(res.format_display())
            return ProviderResponse(
                text="\n".join(results),
                metrics=resp.metrics,
                context=resp.context,
                tool_calls=resp.tool_calls,
                finish_reason=resp.finish_reason,
                raw=resp.raw,
            )

        return ProviderResponse(
            text=normalize_response(resp.text),
            metrics=resp.metrics,
            context=resp.context,
            tool_calls=resp.tool_calls,
            finish_reason=resp.finish_reason,
            raw=resp.raw,
        )

    def resolve_fastpath(self, prompt: str) -> CommandRequest | None:
        """Resolve prompt to a structured CommandRequest via deterministic fastpath."""
        return self.fastpath.resolve(prompt)

    def parse_command_proposal(self, text: str) -> CommandRequest | None:
        """Attempt to parse a structured command proposal from model response."""
        return extract_command_proposal(text)

    def evaluate_command(self, command: str | CommandRequest) -> SafetyAssessment:
        """Evaluate command risk level through the SafetyEngine."""
        return self.safety.evaluate(command)

    def execute_command(self, request: CommandRequest) -> ExecutionResult:
        """Execute a validated CommandRequest via the CommandExecutor."""
        return self.executor.execute(request)
