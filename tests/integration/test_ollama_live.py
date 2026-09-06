"""Integration tests running against a live local Ollama instance."""

import pytest

from avi.config import Config
from avi.core.router import Router
from avi.providers.ollama import OllamaProvider


def is_live_ollama_ready() -> bool:
    provider = OllamaProvider()
    return provider.is_available()


@pytest.mark.skipif(not is_live_ollama_ready(), reason="Local Ollama instance is not running")
def test_live_ollama_streaming():
    config = Config.load()
    router = Router(config)

    chunks = list(router.route("what command shows current directory?", stream=True))
    output = "".join(chunks).strip()

    assert "pwd" in output.lower()
    metrics = router.last_metrics
    assert metrics is not None
    assert metrics.total_duration_ms > 0


@pytest.mark.skipif(not is_live_ollama_ready(), reason="Local Ollama instance is not running")
def test_live_ollama_full_response():
    config = Config.load()
    router = Router(config)

    resp = router.route_full("what command shows the current directory?")
    assert "pwd" in resp.text.lower()
    assert resp.metrics is not None
    assert resp.metrics.total_duration_ms > 0
