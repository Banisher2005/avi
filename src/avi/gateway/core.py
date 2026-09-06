"""Protocol-Independent Gateway Core for AVI.

Serves as the central bridge between external protocols (MCP, JSON-RPC, REST,
etc.) and AVI Core. Guarantees that all external clients pass through the
same ToolRegistry, SafetyEngine, and CommandExecutor pipeline.
"""

import shlex
import time
import uuid
from typing import Any

from avi.config import Config
from avi.context.collectors import collect_snapshot
from avi.core.router import Router
from avi.execution.models import CommandRequest
from avi.gateway.models import (
    GatewayConfirmation,
    GatewayExecutionResponse,
    GatewayToolDefinition,
)
from avi.providers.models import AgentRequest, ToolCall
from avi.safety.models import RiskLevel


DEFAULT_CONFIRMATION_TTL_SECONDS = 300.0  # 5 minutes


class GatewayCore:
    """Protocol-agnostic gateway exposing AVI Core functionality safely."""

    def __init__(
        self,
        router: Router | None = None,
        config: Config | None = None,
        confirmation_ttl: float = DEFAULT_CONFIRMATION_TTL_SECONDS,
    ) -> None:
        self.config = config or Config.load()
        self.router = router or Router(self.config)
        self.confirmation_ttl = confirmation_ttl
        self._pending_confirmations: dict[str, GatewayConfirmation] = {}

    # -----------------------------------------------------------------------
    # Tool Operations
    # -----------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Discover all registered read-only tools and their parameter schemas."""
        tools: list[dict[str, Any]] = []
        for tool in self.router.tools.list_tools():
            tools.append(tool.to_metadata())
        return sorted(tools, key=lambda t: t["name"])

    def get_tool_schema(self, tool_name: str) -> dict[str, Any] | None:
        """Retrieve schema metadata for a specific tool by name."""
        tool = self.router.tools.get(tool_name)
        if tool is None:
            return None
        return tool.to_metadata()

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a tool through ToolRegistry with strict parameter validation."""
        args = arguments or {}
        call = ToolCall(name=tool_name, arguments=args)
        result = self.router.execute_tool_call(call)
        return {
            "tool": tool_name,
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "display": result.format_display(),
        }

    # -----------------------------------------------------------------------
    # Command Safety & Execution Operations
    # -----------------------------------------------------------------------

    def evaluate_command(self, command: str) -> dict[str, Any]:
        """Evaluate command safety risk level through SafetyEngine without executing."""
        assessment = self.router.evaluate_command(command)
        return {
            "command": command,
            "level": assessment.level.value,
            "is_safe": assessment.is_safe,
            "requires_confirmation": assessment.requires_confirmation,
            "is_blocked": assessment.is_blocked,
            "reason": assessment.reason,
        }

    def execute_command(
        self,
        command: str,
        confirmed: bool = False,
        confirmation_token: str | None = None,
    ) -> dict[str, Any]:
        """Execute a command through SafetyEngine and CommandExecutor.

        Hard Invariant:
        Every execution request strictly routes through SafetyEngine.
        - SAFE commands execute immediately via subprocess (shell=False).
        - CONFIRM commands require an explicit confirmation token.
        - BLOCK commands are refused with an explicit diagnostic error.
        """
        assessment = self.router.evaluate_command(command)

        # 1. Catastrophic / Malicious: Blocked unconditionally
        if assessment.is_blocked:
            resp = GatewayExecutionResponse(
                status="blocked",
                command=command,
                reason=assessment.reason,
                error=f"Command blocked by SafetyEngine: {assessment.reason}",
            )
            return resp.to_dict()

        # 2. Mutating / System-modifying: Confirmation required
        if assessment.requires_confirmation:
            if not confirmed or not confirmation_token:
                # Generate new secure confirmation token
                token_obj = self._create_confirmation_token(command, assessment.reason)
                resp = GatewayExecutionResponse(
                    status="confirmation_required",
                    command=command,
                    reason=assessment.reason,
                    confirmation_token=token_obj.token,
                    error="Confirmation required to execute modifying command.",
                )
                return resp.to_dict()

            # Validate submitted confirmation token
            valid = self._validate_and_consume_token(confirmation_token, command)
            if not valid:
                resp = GatewayExecutionResponse(
                    status="error",
                    command=command,
                    error="Invalid or expired confirmation token.",
                )
                return resp.to_dict()

        # 3. Execution: Either SAFE or confirmed CONFIRM
        tokens = shlex.split(command)
        if not tokens:
            resp = GatewayExecutionResponse(
                status="error",
                command=command,
                error="Empty command cannot be executed.",
            )
            return resp.to_dict()

        cmd_req = CommandRequest(
            program=tokens[0],
            args=tokens[1:],
            timeout=self.config.command_timeout,
            max_output_bytes=self.config.max_output_bytes,
        )

        t0 = time.perf_counter()
        exec_result = self.router.execute_command(cmd_req)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        resp = GatewayExecutionResponse(
            status="executed",
            command=command,
            exit_code=exec_result.exit_code,
            stdout=exec_result.stdout,
            stderr=exec_result.stderr,
            display=exec_result.format_display(),
            duration_ms=elapsed_ms,
        )
        return resp.to_dict()

    # -----------------------------------------------------------------------
    # Agent & Context Operations
    # -----------------------------------------------------------------------

    def send_agent_request(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: Any | None = None,
    ) -> dict[str, Any]:
        """Route query through FastPath / Router / configured AIProvider."""
        resp = self.router.route_full(prompt=prompt, context=context)
        metrics_data = None
        if resp.metrics:
            metrics_data = {
                "total_duration_ms": round(resp.metrics.total_duration_ms, 2),
            }
        tool_calls_data = [
            {"name": tc.name, "arguments": tc.arguments, "call_id": tc.call_id}
            for tc in resp.tool_calls
        ]
        return {
            "text": resp.text,
            "tool_calls": tool_calls_data,
            "metrics": metrics_data,
            "context": resp.context,
        }

    def get_context(self, domains: list[str] | None = None) -> dict[str, Any]:
        """Inspect environment context (terminal, git, previous commands)."""
        needed = set(domains) if domains else {"terminal", "git", "prev_cmd"}
        snapshot = collect_snapshot(needed)
        return {
            "terminal": {
                "cwd": snapshot.terminal.cwd,
                "os": snapshot.terminal.os_name,
                "shell": snapshot.terminal.shell,
            } if snapshot.terminal else None,
            "git": {
                "is_repo": snapshot.git.is_repo,
                "root": snapshot.git.root,
                "branch": snapshot.git.branch,
                "is_dirty": snapshot.git.is_dirty,
                "modified_count": snapshot.git.modified_count,
                "untracked_count": snapshot.git.untracked_count,
            } if snapshot.git else None,
            "previous_command": {
                "command": snapshot.previous_command.command,
                "exit_code": snapshot.previous_command.exit_code,
            } if snapshot.previous_command else None,
        }

    # -----------------------------------------------------------------------
    # Health & Capabilities
    # -----------------------------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        """Aggregate health status across Gateway, Router, Tools, and Provider."""
        prov_health = self.router.provider.health_check()
        return {
            "status": "healthy" if prov_health.healthy else "degraded",
            "gateway": "operational",
            "tools_registered": len(self.router.tools),
            "fastpath_templates": len(self.router.fastpath),
            "safety_engine": "active",
            "active_provider": self.config.provider,
            "provider_health": {
                "healthy": prov_health.healthy,
                "message": prov_health.message,
                "latency_ms": prov_health.latency_ms,
                "model": self.router.provider.get_model_name(),
            },
        }

    def get_capabilities(self) -> dict[str, Any]:
        """Expose supported protocols and execution capabilities."""
        prov_caps = self.router.provider.capabilities().to_dict()
        return {
            "protocols": ["jsonrpc-2.0", "mcp-2024-11-05"],
            "transports": ["stdio", "tcp"],
            "tools": {
                "read_only": True,
                "count": len(self.router.tools),
                "names": sorted(self.router.tools.list_names()),
            },
            "safety": {
                "levels": ["SAFE", "CONFIRM", "BLOCK"],
                "fail_closed": True,
                "confirmation_required": True,
            },
            "provider": prov_caps,
        }

    # -----------------------------------------------------------------------
    # Confirmation Token Management
    # -----------------------------------------------------------------------

    def _cleanup_tokens(self) -> None:
        """Remove expired confirmation tokens."""
        now = time.time()
        expired = [k for k, v in self._pending_confirmations.items() if v.expires_at < now]
        for k in expired:
            self._pending_confirmations.pop(k, None)

    def _create_confirmation_token(self, command: str, reason: str) -> GatewayConfirmation:
        """Generate a cryptographically random, time-bounded confirmation token."""
        self._cleanup_tokens()
        token = f"cf-{uuid.uuid4().hex[:16]}"
        now = time.time()
        confirmation = GatewayConfirmation(
            token=token,
            command=command,
            reason=reason,
            created_at=now,
            expires_at=now + self.confirmation_ttl,
        )
        self._pending_confirmations[token] = confirmation
        return confirmation

    def _validate_and_consume_token(self, token: str, command: str) -> bool:
        """Validate single-use token against command and consume it."""
        self._cleanup_tokens()
        record = self._pending_confirmations.get(token)
        if record is None:
            return False

        if record.command.strip() != command.strip():
            return False

        # Consume single-use token immediately
        self._pending_confirmations.pop(token, None)
        return True
