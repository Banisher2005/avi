"""Unit tests for Core Router subsystem."""

import pytest

from avi.config import Config
from avi.core.router import Router
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.providers.ollama import OllamaProvider


class DummyProvider(BaseProvider):
    def __init__(self):
        self._metrics = ResponseMetrics(total_duration_ms=42.0)
        self._context = [1, 2, 3]

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        yield "pwd"

    def generate_full(self, prompt, system_prompt=None, context=None):
        return ProviderResponse(text="pwd", metrics=self._metrics, context=self._context)

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "dummy-model"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return self._context


def test_router_with_custom_provider():
    config = Config.load()
    dummy = DummyProvider()
    router = Router(config, provider=dummy)

    chunks = list(router.route("what command shows current directory?"))
    assert "".join(chunks).strip() == "pwd"

    full = router.route_full("what command shows current directory?")
    assert full.text == "pwd"
    assert full.metrics is not None
    assert full.metrics.total_duration_ms == 42.0
    assert full.context == [1, 2, 3]
    assert router.last_context == [1, 2, 3]
    assert router.warmup() is True


def test_router_initializes_ollama_provider():
    config = Config.load()
    router = Router(config)
    assert isinstance(router.provider, OllamaProvider)
    assert router.provider.get_model_name() == config.model


def test_router_unsupported_provider():
    config = Config.load()
    config.provider = "unsupported_backend"
    with pytest.raises(ValueError) as exc_info:
        Router(config)
    assert "Unsupported provider" in str(exc_info.value)


def test_router_hooks():
    config = Config.load()
    router = Router(config, provider=DummyProvider())
    assert router.check_fast_path("any prompt") is None
    assert router.should_delegate("any prompt") is False
