"""Request routing subsystem for AVI."""

from typing import Any, Iterator

from avi.config import Config
from avi.core.normalizer import normalize_response, normalize_stream
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.providers.ollama import OllamaProvider


class Router:
    """Routes incoming user requests to the appropriate subsystem or model provider."""

    def __init__(self, config: Config, provider: BaseProvider | None = None) -> None:
        self.config = config
        self._provider = provider or self._init_provider(config)

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
        return self._provider.last_metrics

    @property
    def last_context(self) -> Any | None:
        """Return conversation context token state from the last request."""
        return self._provider.last_context

    def warmup(self) -> bool:
        """Trigger backend warmup to ensure sub-second response times."""
        return self._provider.warmup()

    def check_fast_path(self, prompt: str) -> str | None:
        """Deterministic fast-path lookup without invoking an LLM.

        To be extended in Phase 6.
        """
        return None

    def should_delegate(self, prompt: str) -> bool:
        """Check if request requires complex agent delegation (e.g. Antigravity).

        To be extended in Phase 7.
        """
        return False

    def route(
        self,
        prompt: str,
        context: Any | None = None,
        stream: bool = True,
    ) -> Iterator[str]:
        """Route request through fast-path or LLM provider with streaming."""
        # 1. Check Fast Path
        fast_result = self.check_fast_path(prompt)
        if fast_result is not None:
            yield fast_result
            return

        # 2. Local LLM Provider
        raw_stream = self._provider.generate(
            prompt=prompt,
            system_prompt=self.config.system_prompt,
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
    ) -> ProviderResponse:
        """Route request and return complete response with timing metrics and context."""
        fast_result = self.check_fast_path(prompt)
        if fast_result is not None:
            return ProviderResponse(
                text=fast_result,
                metrics=ResponseMetrics(total_duration_ms=0.0),
                context=context,
            )

        resp = self._provider.generate_full(
            prompt=prompt,
            system_prompt=self.config.system_prompt,
            context=context,
        )
        return ProviderResponse(
            text=normalize_response(resp.text),
            metrics=resp.metrics,
            context=resp.context,
        )
