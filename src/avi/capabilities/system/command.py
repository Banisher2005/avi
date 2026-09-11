"""System command execution capability with subprocess isolation and safety policy."""

from pathlib import Path
from typing import Any

from avi.capabilities.models import (
    BaseCapability,
    CapabilityCategory,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.execution.executor import CommandExecutor
from avi.execution.models import CommandRequest
from avi.safety.engine import SafetyEngine
from avi.safety.models import ActionCategory


class ExecuteCommandCapability(BaseCapability):
    """Execute a system terminal command using controlled subprocess execution."""

    name = "system.execute_command"
    description = "Execute a shell command with working directory and bounded execution timeout."
    category = CapabilityCategory.DEVELOPMENT
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command line string to execute (e.g. 'git status', 'ls -la', 'npm test')",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the command (default '.')",
                "default": ".",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds before terminating process (default 15s)",
                "default": 15.0,
            },
            "confirmed": {
                "type": "boolean",
                "description": "Whether the user explicitly confirmed execution of potentially modifying commands",
                "default": False,
            },
        },
        "required": ["command"],
    }
    risk_category = ActionCategory.PRIVILEGED
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True
    tags = ("system", "command", "terminal", "shell", "run", "execute")

    def __init__(
        self,
        executor: CommandExecutor | None = None,
        safety_engine: SafetyEngine | None = None,
    ) -> None:
        self.executor = executor or CommandExecutor()
        self.safety_engine = safety_engine or SafetyEngine()

    def execute(
        self,
        command: str = "",
        cwd: str = ".",
        timeout: float = 15.0,
        confirmed: bool = False,
        **kwargs: Any,
    ) -> CapabilityResult:
        cmd_str = (command or kwargs.get("cmd") or kwargs.get("line") or "").strip()
        if not cmd_str:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Command parameter is required.",
                message="Cannot execute: no command string provided.",
            )

        resolved_cwd = Path(cwd or ".").expanduser().resolve()
        request = CommandRequest.from_command_line(
            cmd_str,
            cwd=resolved_cwd,
            timeout=float(timeout),
        )

        # 1. Safety policy assessment
        assessment = self.safety_engine.evaluate(request)
        if assessment.is_blocked:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Blocked by safety policy: {assessment.reason}",
                message=f"Command '{cmd_str}' was blocked: {assessment.reason}",
                classification=self.data_classification,
            )

        if assessment.requires_confirmation and not confirmed:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.CONFIRMATION_REQUIRED,
                data={"command": cmd_str, "cwd": str(resolved_cwd), "assessment": assessment},
                message=f"Command '{cmd_str}' can modify system state. Please confirm execution.",
                classification=self.data_classification,
            )

        # 2. Controlled subprocess execution
        try:
            exec_res = self.executor.execute(request)
            success = exec_res.exit_code == 0
            status = ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILED
            display_msg = exec_res.stdout.strip() or exec_res.stderr.strip() or f"Command finished with exit code {exec_res.exit_code}."
            return CapabilityResult(
                success=success,
                status=status,
                data={
                    "command": cmd_str,
                    "exit_code": exec_res.exit_code,
                    "stdout": exec_res.stdout,
                    "stderr": exec_res.stderr,
                    "duration_ms": exec_res.duration_ms,
                    "cwd": str(resolved_cwd),
                },
                message=display_msg,
                error=exec_res.error or (exec_res.stderr if not success else None),
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Command execution failed: {err}",
                classification=self.data_classification,
            )
