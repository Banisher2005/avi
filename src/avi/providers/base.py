"""Base provider abstraction for AI model backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator


@dataclass
class ResponseMetrics:
    """Performance and latency metrics for an inference request."""

    total_duration_ms: float
    load_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    eval_duration_ms: float | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None


@dataclass
class ProviderResponse:
    """Full response from a model provider."""

    text: str
    metrics: ResponseMetrics | None = None


class BaseProvider(ABC):
    """Abstract base class for all model backends (Ollama, Antigravity, etc.)."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        stream: bool = True,
    ) -> Iterator[str]:
        """Stream or generate response chunks for a prompt."""
        pass

    @abstractmethod
    def generate_full(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> ProviderResponse:
        """Generate a complete response along with metrics."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the backend is reachable and ready."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the active model name."""
        pass

    @property
    @abstractmethod
    def last_metrics(self) -> ResponseMetrics | None:
        """Return the metrics from the most recent generation, if available."""
        pass
