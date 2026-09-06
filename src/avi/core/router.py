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

# Regex patterns for deterministic fast-path context queries
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
    r"^(what\s+(is\s+my\s+|the\s+)?(operating\s+system|os)(\s+(am\s+i\s+on|is\s+this|am\s+i\s+using))?|current\s+os)$",
    re.IGNORECASE,
)


class Router:
    """Routes incoming user requests to the appropriate subsystem or model provider."""

    def __init__(self, config: Config, provider: BaseProvider | None = None) -> None:
        self.config = config
        self._provider = provider or self._init_provider(config)
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
        """Deterministic fast-path lookup without invoking an LLM.

        Resolves common terminal and environment queries in 0-1 ms.
        """
        normalized = prompt.strip().rstrip("?.!").strip()
        t0 = time.perf_counter()

        result = None
        # 1. Directory / CWD
        if _FAST_PATH_CWD.match(normalized):
            result = collect_terminal_context().cwd
        # 2. Git Branch
        elif _FAST_PATH_BRANCH.match(normalized):
            git_ctx = collect_git_context()
            if git_ctx.is_repo:
                result = git_ctx.branch or "HEAD"
            else:
                result = "Not in a Git repository."
        # 3. Shell
        elif _FAST_PATH_SHELL.match(normalized):
            result = collect_terminal_context().shell
        # 4. Operating System
        elif _FAST_PATH_OS.match(normalized):
            result = collect_terminal_context().os_name

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
        """Route request through fast-path or LLM provider with streaming."""
        # 1. Check Fast Path
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
