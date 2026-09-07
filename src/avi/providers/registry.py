"""Provider Registry and Factory for AVI AI Providers.

Enables dynamic registration, discovery, and instantiation of model providers
without modifying AVI Core, Router, SafetyEngine, or CommandExecutor.
"""

from typing import Any, Callable

from avi.config import Config
from avi.providers.base import BaseProvider
from avi.providers.models import ProviderCapabilities, ProviderNotAvailableError

ProviderFactory = Callable[[Config, dict[str, Any]], BaseProvider]


class ProviderRegistry:
    """Registry maintaining available AI provider factories."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., BaseProvider]] = {}

    def register(
        self,
        name: str,
        factory: Callable[..., BaseProvider],
    ) -> None:
        """Register a provider factory under a given name."""
        self._factories[name.lower().strip()] = factory

    def get(
        self,
        name: str,
        config: Config | None = None,
        **kwargs: Any,
    ) -> BaseProvider:
        """Retrieve and instantiate a provider by name."""
        canonical_name = name.lower().strip()
        factory = self._factories.get(canonical_name)
        if factory is None:
            available = ", ".join(sorted(self._factories.keys()))
            raise ProviderNotAvailableError(
                f"Unsupported provider: '{name}'. Available providers: {available}"
            )
        cfg = config or Config()
        return factory(cfg, **kwargs)

    def list_providers(self) -> list[str]:
        """List names of all registered providers."""
        return sorted(self._factories.keys())

    def remove(self, name: str) -> None:
        """Remove a provider from the registry."""
        self._factories.pop(name.lower().strip(), None)

    def has_provider(self, name: str) -> bool:
        """Check if a provider is registered."""
        return name.lower().strip() in self._factories

    def __contains__(self, name: str) -> bool:
        return self.has_provider(name)

    def __len__(self) -> int:
        return len(self._factories)

    def select_provider(
        self,
        required: ProviderCapabilities,
        config: Config | None = None,
        active_provider: BaseProvider | None = None,
    ) -> BaseProvider | None:
        """Select a suitable provider satisfying required capabilities without hard-coding names."""
        if active_provider is not None:
            try:
                caps = active_provider.capabilities()
                if caps.supports(required) and active_provider.is_available():
                    return active_provider
            except Exception:
                pass

        cfg = config or Config()
        for name in self.list_providers():
            try:
                candidate = self.get(name, config=cfg)
                caps = candidate.capabilities()
                if caps.supports(required) and candidate.is_available():
                    return candidate
            except Exception:
                continue

        return None


def _create_ollama_provider(config: Config, **kwargs: Any) -> BaseProvider:
    from avi.providers.ollama import OllamaProvider

    host = kwargs.get("host", config.host)
    model = kwargs.get("model", config.model)
    timeout = kwargs.get("timeout", config.timeout)
    temperature = kwargs.get("temperature", config.temperature)
    keep_alive = kwargs.get("keep_alive", config.keep_alive)
    return OllamaProvider(
        host=host,
        model=model,
        timeout=timeout,
        temperature=temperature,
        keep_alive=keep_alive,
    )


def _create_antigravity_provider(config: Config, **kwargs: Any) -> BaseProvider:
    from avi.providers.antigravity import AntigravityProvider

    model = kwargs.get(
        "model", getattr(config, "antigravity_model", None) or "gemini-3.8-flash-high"
    )
    binary = kwargs.get("binary", getattr(config, "antigravity_bin", None))
    timeout = kwargs.get("timeout", config.timeout)
    runner = kwargs.get("runner")
    return AntigravityProvider(
        model=model,
        binary=binary,
        timeout=timeout,
        runner=runner,
    )


def create_default_provider_registry() -> ProviderRegistry:
    """Create and initialize registry with default built-in providers."""
    registry = ProviderRegistry()
    registry.register("ollama", _create_ollama_provider)
    registry.register("local", _create_ollama_provider)
    registry.register("antigravity", _create_antigravity_provider)
    return registry


# Global default registry instance
_DEFAULT_REGISTRY: ProviderRegistry | None = None


def get_default_registry() -> ProviderRegistry:
    """Return singleton default provider registry."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = create_default_provider_registry()
    return _DEFAULT_REGISTRY


def register_provider(name: str, factory: Callable[..., BaseProvider]) -> None:
    """Register a provider in the global registry."""
    get_default_registry().register(name, factory)


def get_provider(name: str, config: Config | None = None, **kwargs: Any) -> BaseProvider:
    """Instantiate a provider from the global registry."""
    return get_default_registry().get(name, config=config, **kwargs)


def list_providers() -> list[str]:
    """List all providers in the global registry."""
    return get_default_registry().list_providers()


def remove_provider(name: str) -> None:
    """Remove a provider from the global registry."""
    get_default_registry().remove(name)


def select_provider(
    required: ProviderCapabilities,
    config: Config | None = None,
    active_provider: BaseProvider | None = None,
) -> BaseProvider | None:
    """Select a suitable provider from the global registry satisfying required capabilities."""
    return get_default_registry().select_provider(
        required=required, config=config, active_provider=active_provider
    )
