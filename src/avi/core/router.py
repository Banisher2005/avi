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
from avi.core.normalizer import normalize_response, normalize_stream
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.providers.ollama import OllamaProvider
from avi.tools.registry import ToolRegistry, create_default_registry

# Regex patterns for deterministic fast-path context and tool queries
_FAST_PATH_CWD = re.compile(
    r"^(what\s+(is\s+my\s+|the\s+)?(current\s+)?directory(\s+(am\s+i\s+in|is\s+this))?|where\s+am\s+i|current\s+directory|pwd)$",
    re.IGNORECASE,
)
_FAST_PATH_BRANCH = re.compile(
    r"^(what\s+(is\s+my\s+|the\s+)?(git\s+)?branch(\s+(am\s+i\s+on|is\s+this))?|current\s+(git\s+)?branch|git\s+branch)$",
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
    ) -> None:
        self.config = config
        self._provider = provider or self._init_provider(config)
        self.tools = tools or create_default_registry()
        self._fast_path_metrics: ResponseMetrics | None = None

    def _init_provider(self, config: Config) -> BaseProvider:
        """Initialize provider based on configuration."""
        if config.provider == "ollama":
            return OllamaProvider(
                host=config.host,
                model=config.model,
                timeout=config.timeout,
                temperature=config.temperature,
                keep_alive=config.keep_alive,
            )
        # Future providers (e.g. Antigravity) will be registered here
        raise ValueError(f"Unsupported provider: '{config.provider}'")

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

        # 2. Lazy Context Assembly
        system_prompt = self.assemble_system_prompt(prompt, explicit_snapshot=explicit_snapshot)

        # 3. Local LLM Provider
        raw_stream = self._provider.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
            stream=stream,
        )

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

        system_prompt = self.assemble_system_prompt(prompt, explicit_snapshot=explicit_snapshot)

        resp = self._provider.generate_full(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
        )
        return ProviderResponse(
            text=normalize_response(resp.text),
            metrics=resp.metrics,
            context=resp.context,
        )
