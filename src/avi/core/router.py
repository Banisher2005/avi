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
from avi.core.fastpath import resolve_command_template
from avi.core.normalizer import normalize_response, normalize_stream
from avi.execution import CommandExecutor, CommandRequest, ExecutionResult, extract_command_proposal
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.providers.ollama import OllamaProvider
from avi.safety import RiskLevel, SafetyAssessment, SafetyEngine
from avi.tools.registry import ToolRegistry, create_default_registry

_FAST_PATH_CWD = re.compile(r"^(what\s+(is\s+my\s+|the\s+)?(current\s+)?directory(\s+(am\s+i\s+in|is\s+this))?|where\s+am\s+i|current\s+directory|pwd)$", re.I)
_FAST_PATH_BRANCH = re.compile(r"^(what\s+(is\s+my\s+|the\s+)?(git\s+)?branch(\s+(am\s+i\s+on|is\s+this))?|(show\s+(me\s+|my\s+)?(current\s+)?|current\s+)(git\s+)?branch|git\s+branch)$", re.I)
_FAST_PATH_SHELL = re.compile(r"^(what\s+(is\s+my\s+|the\s+)?shell(\s+(am\s+i\s+using|is\s+this))?|current\s+shell)$", re.I)
_FAST_PATH_OS = re.compile(r"^(what\s+(is\s+my\s+|the\s+)?(operating\s+system|os)(\s+(am\s+i\s+on|is\s+this|am\s+i\s+using))?|current\s+os|system\s+info)$", re.I)
_FAST_PATH_FILES = re.compile(r"^(what\s+files\s+(are\s+)?(here|in\s+this\s+directory)|list\s+files(\s+in\s+this\s+directory|\s+here)?|show\s+files(\s+here)?|ls)$", re.I)
_FAST_PATH_RAM = re.compile(r"^(what(\'s|\s+is)\s+using\s+(the\s+most\s+)?(ram|memory)|show\s+processes|top\s+processes|list\s+processes)$", re.I)
_FAST_PATH_DISK = re.compile(r"^(how\s+much\s+disk\s+space\s+(do\s+i\s+have|is\s+left)|disk\s+usage|check\s+disk\s+space|disk\s+space)$", re.I)
_FAST_PATH_GIT_STATUS = re.compile(r"^(git\s+status|is\s+(the\s+|my\s+)?repo\s+clean|check\s+git\s+status)$", re.I)
_FAST_PATH_GIT_LOG = re.compile(r"^(recent\s+commits|git\s+log|show\s+commits|git\s+history)$", re.I)


class Router:
    """Routes incoming user requests to tools, fast-path handlers, or model providers."""

    def __init__(self, config: Config, provider: BaseProvider | None = None, tools: ToolRegistry | None = None, safety: SafetyEngine | None = None, executor: CommandExecutor | None = None) -> None:
        self.config = config
        self._provider = provider or self._init_provider(config)
        self.tools = tools or create_default_registry()
        self.safety = safety or SafetyEngine()
        self.executor = executor or CommandExecutor(default_timeout=config.command_timeout, default_max_output_bytes=config.max_output_bytes)
        self._fast_path_metrics: ResponseMetrics | None = None

    def _init_provider(self, config: Config) -> BaseProvider:
        if config.provider == "ollama":
            return OllamaProvider(host=config.host, model=config.model, timeout=config.timeout, temperature=config.temperature, keep_alive=config.keep_alive)
        raise ValueError(f"Unsupported provider: '{config.provider}'")

    @property
    def provider(self) -> BaseProvider:
        return self._provider

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        return self._fast_path_metrics or self._provider.last_metrics

    @property
    def last_context(self) -> Any | None:
        return self._provider.last_context

    def warmup(self) -> bool:
        return self._provider.warmup()

    def check_fast_path(self, prompt: str) -> str | None:
        """Resolve deterministic environment, tool, and common command-template requests."""
        normalized = prompt.strip().rstrip("?.!").strip()
        t0 = time.perf_counter()
        result = None

        if _FAST_PATH_CWD.match(normalized):
            result = collect_terminal_context().cwd
        elif _FAST_PATH_BRANCH.match(normalized):
            tool = self.tools.get("git.branch")
            result = tool.execute().format_display() if tool else (collect_git_context().branch if collect_git_context().is_repo else "Not in a Git repository.")
        elif _FAST_PATH_SHELL.match(normalized):
            result = collect_terminal_context().shell
        elif _FAST_PATH_OS.match(normalized):
            tool = self.tools.get("system.system_info")
            result = tool.execute().format_display() if tool and "system info" in normalized.lower() else collect_terminal_context().os_name
        elif _FAST_PATH_FILES.match(normalized):
            tool = self.tools.get("filesystem.list_directory")
            if tool:
                result = tool.execute().format_display()
        elif _FAST_PATH_RAM.match(normalized):
            tool = self.tools.get("system.processes")
            if tool:
                result = tool.execute(limit=10, sort_by="memory").format_display()
        elif _FAST_PATH_DISK.match(normalized):
            tool = self.tools.get("system.disk_usage")
            if tool:
                result = tool.execute(path="/").format_display()
        elif _FAST_PATH_GIT_STATUS.match(normalized):
            tool = self.tools.get("git.status")
            if tool:
                result = tool.execute().format_display()
        elif _FAST_PATH_GIT_LOG.match(normalized):
            tool = self.tools.get("git.log")
            if tool:
                result = tool.execute(limit=5).format_display()
        else:
            result = resolve_command_template(normalized)

        if result is not None:
            self._fast_path_metrics = ResponseMetrics(total_duration_ms=(time.perf_counter() - t0) * 1000.0)
            return result
        self._fast_path_metrics = None
        return None

    def should_delegate(self, prompt: str) -> bool:
        return False

    def assemble_system_prompt(self, prompt: str, explicit_snapshot: ContextSnapshot | None = None) -> str:
        base_prompt = self.config.system_prompt
        if explicit_snapshot is not None:
            context_block = explicit_snapshot.to_prompt_context()
        else:
            needed = detect_needed_context(prompt)
            if not needed:
                return base_prompt
            context_block = collect_snapshot(needed).to_prompt_context()
        return f"{base_prompt}\n\nContext:\n{context_block}" if context_block else base_prompt

    def route(self, prompt: str, context: Any | None = None, stream: bool = True, explicit_snapshot: ContextSnapshot | None = None) -> Iterator[str]:
        fast_result = self.check_fast_path(prompt)
        if fast_result is not None:
            yield fast_result
            return
        system_prompt = self.assemble_system_prompt(prompt, explicit_snapshot=explicit_snapshot)
        raw_stream = self._provider.generate(prompt=prompt, system_prompt=system_prompt, context=context, stream=stream)
        yield from normalize_stream(raw_stream) if stream else [normalize_response("".join(raw_stream))]

    def route_full(self, prompt: str, context: Any | None = None, explicit_snapshot: ContextSnapshot | None = None) -> ProviderResponse:
        fast_result = self.check_fast_path(prompt)
        if fast_result is not None:
            return ProviderResponse(text=fast_result, metrics=self._fast_path_metrics, context=context)
        system_prompt = self.assemble_system_prompt(prompt, explicit_snapshot=explicit_snapshot)
        resp = self._provider.generate_full(prompt=prompt, system_prompt=system_prompt, context=context)
        return ProviderResponse(text=normalize_response(resp.text), metrics=resp.metrics, context=resp.context)

    def parse_command_proposal(self, text: str) -> CommandRequest | None:
        return extract_command_proposal(text)

    def evaluate_command(self, command: str | CommandRequest) -> SafetyAssessment:
        return self.safety.evaluate(command)

    def execute_command(self, request: CommandRequest) -> ExecutionResult:
        return self.executor.execute(request)
