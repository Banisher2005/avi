"""Ollama provider implementation for local model inference."""

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics


class OllamaError(Exception):
    """Base exception for Ollama provider errors."""
    pass


class OllamaConnectionError(OllamaError):
    """Raised when Ollama is unreachable or connection is refused."""
    pass


class OllamaModelNotFoundError(OllamaError):
    """Raised when the specified model is not found in Ollama."""
    pass


class OllamaTimeoutError(OllamaError):
    """Raised when a request to Ollama times out."""
    pass


class OllamaAPIError(OllamaError):
    """Raised when Ollama returns an unexpected API error."""
    pass


class OllamaProvider(BaseProvider):
    """Provider communicating directly with the Ollama HTTP API."""

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:1.5b",
        timeout: float = 30.0,
        temperature: float = 0.1,
        keep_alive: str = "5m",
    ) -> None:
        self.host = host.rstrip("/")
        if not (self.host.startswith("http://") or self.host.startswith("https://")):
            self.host = f"http://{self.host}"
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.keep_alive = keep_alive
        self._last_metrics: ResponseMetrics | None = None
        self._last_context: list[int] | None = None

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        """Metrics from the most recent request."""
        return self._last_metrics

    @property
    def last_context(self) -> list[int] | None:
        """Conversation context token array from the most recent request."""
        return self._last_context

    def get_model_name(self) -> str:
        """Return the active model name."""
        return self.model

    def is_available(self) -> bool:
        """Check if the Ollama server is running and accessible."""
        url = f"{self.host}/api/version"
        req = urllib.request.Request(url, headers={"User-Agent": "avi/0.1.0"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return resp.status == 200
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            return False

    def warmup(self) -> bool:
        """Warm up the model weights into GPU VRAM / system RAM."""
        url = f"{self.host}/api/generate"
        payload: dict[str, Any] = {
            "model": self.model,
            "keep_alive": self.keep_alive,
        }
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "avi/0.1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            return False

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: list[int] | None = None,
        stream: bool = True,
    ) -> Iterator[str]:
        """Stream or yield text chunks from Ollama /api/generate."""
        url = f"{self.host}/api/generate"
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": self.temperature,
            },
            "keep_alive": self.keep_alive,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if context:
            payload["context"] = context

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "avi/0.1.0",
            },
            method="POST",
        )

        start_time = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if stream:
                    for line in resp:
                        if not line:
                            continue
                        line_str = line.decode("utf-8").strip()
                        if not line_str:
                            continue

                        try:
                            data = json.loads(line_str)
                        except json.JSONDecodeError as err:
                            raise OllamaAPIError(f"Invalid JSON from Ollama: {line_str}") from err

                        if "error" in data:
                            self._handle_api_error(data["error"])

                        chunk = data.get("response", "")
                        if chunk:
                            yield chunk

                        if data.get("done", False):
                            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                            self._last_metrics = self._parse_metrics(data, elapsed_ms)
                            if "context" in data:
                                self._last_context = data.get("context")
                else:
                    body = resp.read().decode("utf-8")
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError as err:
                        raise OllamaAPIError(f"Invalid JSON from Ollama: {body}") from err

                    if "error" in data:
                        self._handle_api_error(data["error"])

                    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                    self._last_metrics = self._parse_metrics(data, elapsed_ms)
                    if "context" in data:
                        self._last_context = data.get("context")
                    yield data.get("response", "")

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
                err_json = json.loads(error_body)
                msg = err_json.get("error", error_body)
            except Exception:
                msg = error_body or e.reason

            if e.code == 404 or "not found" in str(msg).lower():
                raise OllamaModelNotFoundError(
                    f"Model '{self.model}' not found in Ollama. "
                    f"Pull it using: ollama pull {self.model}"
                ) from e
            raise OllamaAPIError(f"Ollama returned HTTP {e.code}: {msg}") from e

        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise OllamaTimeoutError(
                    f"Connection to Ollama timed out after {self.timeout}s."
                ) from e
            raise OllamaConnectionError(
                f"Could not connect to Ollama at {self.host}. "
                "Is Ollama running? Start it with: ollama serve"
            ) from e

        except (socket.timeout, TimeoutError) as e:
            raise OllamaTimeoutError(
                f"Connection to Ollama timed out after {self.timeout}s."
            ) from e

    def generate_full(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: list[int] | None = None,
    ) -> ProviderResponse:
        """Generate a complete response with timing metrics and context."""
        chunks = list(self.generate(prompt=prompt, system_prompt=system_prompt, context=context, stream=False))
        return ProviderResponse(
            text="".join(chunks),
            metrics=self.last_metrics,
            context=self.last_context,
        )

    def _handle_api_error(self, error_message: str) -> None:
        """Handle error messages in Ollama JSON response."""
        if "not found" in error_message.lower():
            raise OllamaModelNotFoundError(
                f"Model '{self.model}' not found in Ollama. "
                f"Pull it using: ollama pull {self.model}"
            )
        raise OllamaAPIError(f"Ollama error: {error_message}")

    def _parse_metrics(self, data: dict[str, Any], fallback_elapsed_ms: float) -> ResponseMetrics:
        """Parse Ollama duration metrics (returned in nanoseconds)."""
        total_ns = data.get("total_duration")
        load_ns = data.get("load_duration")
        prompt_eval_ns = data.get("prompt_eval_duration")
        eval_ns = data.get("eval_duration")

        total_ms = (total_ns / 1_000_000.0) if total_ns is not None else fallback_elapsed_ms
        load_ms = (load_ns / 1_000_000.0) if load_ns is not None else None
        prompt_eval_ms = (prompt_eval_ns / 1_000_000.0) if prompt_eval_ns is not None else None
        eval_ms = (eval_ns / 1_000_000.0) if eval_ns is not None else None

        return ResponseMetrics(
            total_duration_ms=total_ms,
            load_duration_ms=load_ms,
            prompt_eval_duration_ms=prompt_eval_ms,
            eval_duration_ms=eval_ms,
            prompt_eval_count=data.get("prompt_eval_count"),
            eval_count=data.get("eval_count"),
        )
