"""Unit tests for Ollama provider with mocked HTTP endpoints."""

import io
import json
import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from avi.providers.ollama import (
    OllamaAPIError,
    OllamaConnectionError,
    OllamaModelNotFoundError,
    OllamaProvider,
    OllamaTimeoutError,
)


class MockHTTPResponse:
    def __init__(self, lines: list[str], status: int = 200):
        self._lines = [line.encode("utf-8") for line in lines]
        self._iter = iter(self._lines)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)

    def read(self):
        return b"".join(self._lines)


def test_ollama_provider_init():
    provider = OllamaProvider(
        host="http://localhost:11434/",
        model="test-model",
        timeout=10.0,
        temperature=0.2,
    )
    assert provider.host == "http://localhost:11434"
    assert provider.get_model_name() == "test-model"
    assert provider.timeout == 10.0
    assert provider.temperature == 0.2


def test_ollama_provider_stream_success():
    provider = OllamaProvider()
    stream_lines = [
        json.dumps({"response": "p", "done": False}) + "\n",
        json.dumps({"response": "wd", "done": False}) + "\n",
        json.dumps({
            "response": "",
            "done": True,
            "total_duration": 150_000_000,
            "load_duration": 1_000_000,
            "prompt_eval_duration": 50_000_000,
            "eval_duration": 90_000_000,
            "prompt_eval_count": 20,
            "eval_count": 2,
        }) + "\n",
    ]

    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(stream_lines)):
        chunks = list(provider.generate("what command shows current directory?", stream=True))
        assert chunks == ["p", "wd"]

        metrics = provider.last_metrics
        assert metrics is not None
        assert metrics.total_duration_ms == 150.0
        assert metrics.load_duration_ms == 1.0
        assert metrics.eval_duration_ms == 90.0
        assert metrics.eval_count == 2


def test_ollama_provider_generate_full():
    provider = OllamaProvider()
    response_body = json.dumps({
        "response": "pwd",
        "done": True,
        "total_duration": 120_000_000,
    }) + "\n"

    with patch("urllib.request.urlopen", return_value=MockHTTPResponse([response_body])):
        result = provider.generate_full("what command shows current directory?")
        assert result.text == "pwd"
        assert result.metrics is not None
        assert result.metrics.total_duration_ms == 120.0


def test_ollama_connection_error():
    provider = OllamaProvider(host="http://127.0.0.1:99999")

    url_error = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    with patch("urllib.request.urlopen", side_effect=url_error):
        with pytest.raises(OllamaConnectionError) as exc_info:
            list(provider.generate("pwd"))
        assert "Could not connect to Ollama" in str(exc_info.value)
        assert "ollama serve" in str(exc_info.value)


def test_ollama_timeout_error():
    provider = OllamaProvider()

    url_error = urllib.error.URLError(socket.timeout("timed out"))
    with patch("urllib.request.urlopen", side_effect=url_error):
        with pytest.raises(OllamaTimeoutError) as exc_info:
            list(provider.generate("test"))
        assert "timed out" in str(exc_info.value)


def test_ollama_model_not_found_http_404():
    provider = OllamaProvider(model="nonexistent:latest")

    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/generate",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b'{"error":"model \'nonexistent:latest\' not found"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(OllamaModelNotFoundError) as exc_info:
            list(provider.generate("test"))
        assert "not found in Ollama" in str(exc_info.value)
        assert "ollama pull nonexistent:latest" in str(exc_info.value)


def test_ollama_malformed_json_response():
    provider = OllamaProvider()
    bad_lines = ["not valid json at all\n"]

    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(bad_lines)):
        with pytest.raises(OllamaAPIError) as exc_info:
            list(provider.generate("test"))
        assert "Invalid JSON" in str(exc_info.value)


def test_is_available():
    provider = OllamaProvider()

    # When available (200)
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(['{"version":"0.33.3"}'], status=200)):
        assert provider.is_available() is True

    # When unavailable (connection error)
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Refused")):
        assert provider.is_available() is False


def test_ollama_provider_context_handling():
    provider = OllamaProvider()
    response_body = json.dumps({
        "response": "AVI",
        "done": True,
        "total_duration": 100_000_000,
        "context": [101, 102, 103],
    }) + "\n"

    with patch("urllib.request.urlopen", return_value=MockHTTPResponse([response_body])):
        result = provider.generate_full("what is my project called?", context=[101, 102])
        assert result.text == "AVI"
        assert result.context == [101, 102, 103]
        assert provider.last_context == [101, 102, 103]


def test_ollama_provider_warmup_success():
    provider = OllamaProvider()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(['{"done":true,"done_reason":"load"}'], status=200)):
        assert provider.warmup() is True


def test_ollama_provider_warmup_failure():
    provider = OllamaProvider()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Refused")):
        assert provider.warmup() is False
