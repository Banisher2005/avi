"""Antigravity provider adapter for AVI.

Provides integration with Antigravity CLI (agy) behind the AIProvider abstraction.
Zero Antigravity-specific types or logic leak into AVI Core or SafetyEngine.
"""

import json
import os
import shutil
import subprocess
import time
from typing import Any, Callable, Iterator

from avi.providers.base import (
    AgentRequest,
    AgentResponse,
    BaseProvider,
    ProviderCapabilities,
    ProviderHealth,
    ResponseMetrics,
    ToolCall,
)
from avi.providers.models import (
    ProviderAPIError,
    ProviderConnectionError,
    ProviderError,
    ProviderNotAvailableError,
    ProviderTimeoutError,
)

DEFAULT_ANTIGRAVITY_MODEL = "gemini-3.8-flash-high"
DEFAULT_ANTIGRAVITY_TIMEOUT = 30.0


def _default_subprocess_runner(
    cmd: list[str],
    timeout: float,
) -> tuple[int, str, str]:
    """Execute command safely via subprocess without shell expansion."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,  # Strict security requirement
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired as err:
        proc.kill()
        proc.communicate()
        raise ProviderTimeoutError(f"Antigravity CLI timed out after {timeout}s.") from err
    except FileNotFoundError as err:
        raise ProviderNotAvailableError(f"Antigravity CLI executable not found: {cmd[0]}") from err
    except OSError as err:
        raise ProviderConnectionError(f"Failed to execute Antigravity CLI: {err}") from err


class AntigravityProvider(BaseProvider):
    """Adapter for Antigravity implementing the standard AIProvider interface."""

    def __init__(
        self,
        model: str = DEFAULT_ANTIGRAVITY_MODEL,
        binary: str | None = None,
        timeout: float = DEFAULT_ANTIGRAVITY_TIMEOUT,
        runner: Callable[[list[str], float], tuple[int, str, str]] | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.binary = binary or os.getenv("AVI_ANTIGRAVITY_BIN") or shutil.which("agy") or "agy"
        self._runner = runner or _default_subprocess_runner
        self._last_metrics: ResponseMetrics | None = None
        self._last_context: Any | None = None

    @property
    def last_metrics(self) -> ResponseMetrics | None:
        """Metrics from the most recent generation."""
        return self._last_metrics

    @property
    def last_context(self) -> Any | None:
        """Context state from the most recent generation."""
        return self._last_context

    def get_model_name(self) -> str:
        """Return the active model name."""
        return self.model

    def capabilities(self) -> ProviderCapabilities:
        """Return capabilities supported by the Antigravity adapter."""
        return ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            structured_output=True,
            vision=False,
            reasoning=True,
            context_size=32768,
            local=True,
            remote=True,
        )

    def health_check(self) -> ProviderHealth:
        """Check if Antigravity CLI is available and functional."""
        resolved = shutil.which(self.binary)
        if not resolved and self._runner is _default_subprocess_runner:
            return ProviderHealth(
                healthy=False,
                message=f"Antigravity executable '{self.binary}' not found in PATH.",
                details={"binary": self.binary},
            )

        t0 = time.perf_counter()
        try:
            code, stdout, stderr = self._runner([self.binary, "--version"], timeout=5.0)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if code == 0:
                version_str = stdout.strip() or "available"
                return ProviderHealth(
                    healthy=True,
                    message=f"Antigravity CLI ready ({version_str})",
                    latency_ms=elapsed_ms,
                    details={"binary": self.binary, "version": version_str},
                )
            return ProviderHealth(
                healthy=False,
                message=f"Antigravity CLI returned exit code {code}: {stderr.strip()}",
                latency_ms=elapsed_ms,
                details={"binary": self.binary, "code": code},
            )
        except ProviderError as err:
            return ProviderHealth(
                healthy=False,
                message=f"Antigravity health check failed: {err}",
                details={"binary": self.binary, "error": str(err)},
            )
        except Exception as err:
            return ProviderHealth(
                healthy=False,
                message=f"Unexpected error during health check: {err}",
                details={"binary": self.binary, "error": str(err)},
            )

    def is_available(self) -> bool:
        """Check if Antigravity provider is ready."""
        return self.health_check().healthy

    def warmup(self) -> bool:
        """Warm up Antigravity backend."""
        return self.is_available()

    def send(self, request: AgentRequest) -> AgentResponse:
        """Execute request via Antigravity CLI and return normalized response."""
        full_prompt = request.prompt
        if request.system_prompt:
            full_prompt = f"System: {request.system_prompt}\n\nUser: {request.prompt}"

        cmd = [
            self.binary,
            "--print",
            full_prompt,
            "--output-format",
            "text",
        ]
        if self.model:
            cmd.extend(["--model", self.model])

        t0 = time.perf_counter()
        code, stdout, stderr = self._runner(cmd, timeout=self.timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self._last_metrics = ResponseMetrics(total_duration_ms=elapsed_ms)
        self._last_context = request.context

        if code != 0:
            err_msg = stderr.strip() or stdout.strip() or f"Exit code {code}"
            raise ProviderAPIError(f"Antigravity execution failed: {err_msg}")

        output_text = stdout.strip()

        # Parse any structured tool calls from output if present
        tool_calls = self._extract_tool_calls(output_text)

        return AgentResponse(
            text=output_text,
            metrics=self._last_metrics,
            context=self._last_context,
            tool_calls=tool_calls,
        )

    def stream(self, request: AgentRequest) -> Iterator[str]:
        """Stream response chunks from Antigravity."""
        # Non-streaming CLI print mode yields the normalized output
        resp = self.send(request)
        if resp.text:
            yield resp.text

    def _extract_tool_calls(self, text: str) -> list[ToolCall]:
        """Extract any structured tool calls formatted in model output."""
        tool_calls: list[ToolCall] = []
        if not text:
            return tool_calls

        # Check for json tool call block: {"tool": "name", "arguments": {...}}
        trimmed = text.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                if isinstance(data, dict) and "tool" in data:
                    name = str(data["tool"])
                    args = data.get("arguments", {})
                    if isinstance(args, dict):
                        tool_calls.append(ToolCall(name=name, arguments=args))
            except json.JSONDecodeError:
                pass

        return tool_calls
