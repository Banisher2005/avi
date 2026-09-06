"""Data models for command execution requests and results."""

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_COMMAND_TIMEOUT: float = 10.0
DEFAULT_MAX_OUTPUT_BYTES: int = 65536  # 64 KB output buffer limit


@dataclass
class CommandRequest:
    """Structured representation of a proposed command execution."""

    program: str
    args: list[str] = field(default_factory=list)
    cwd: Path | str | None = None
    timeout: float = DEFAULT_COMMAND_TIMEOUT
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    env: dict[str, str] | None = None

    @property
    def command_line(self) -> str:
        """Formatted shell-safe display string."""
        if not self.program:
            return ""
        return shlex.join([self.program] + list(self.args))

    def get_cwd(self) -> Path:
        """Resolve current working directory."""
        if self.cwd is not None:
            p = Path(self.cwd)
            if p.is_dir():
                return p.resolve()
        return Path.cwd().resolve()


@dataclass
class ExecutionResult:
    """Structured result returned from executing a command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    output_truncated: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        """Indicates whether execution completed with zero exit code."""
        return self.exit_code == 0 and not self.timed_out and not self.error

    def format_display(self) -> str:
        """Format the execution output for terminal display."""
        parts = []
        if self.stdout:
            parts.append(self.stdout.rstrip("\n"))
        if self.stderr:
            parts.append(self.stderr.rstrip("\n"))
        if self.timed_out:
            parts.append("[Command timed out after execution limit]")
        return "\n".join(parts)


def extract_command_proposal(text: str) -> CommandRequest | None:
    """Extract a structured CommandRequest from model output if proposed.

    Returns None if the output is not a valid structured proposal.
    """
    stripped = text.strip()
    if not stripped:
        return None

    # 1. Check for JSON structured proposal: {"action": "execute", "command": "..."} or {"command": "..."}
    json_match = re.search(r"\{[^{}]*\"command\"\s*:\s*\"([^\"]+)\"[^{}]*\}", stripped)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            cmd_str = data.get("command", "").strip()
            if cmd_str:
                tokens = shlex.split(cmd_str)
                if tokens:
                    return CommandRequest(program=tokens[0], args=tokens[1:])
        except Exception:
            return None

    # 2. Check for prefix lines: COMMAND: <cmd> or PROPOSAL: <cmd>
    for line in stripped.splitlines():
        line_clean = line.strip()
        for prefix in ("COMMAND:", "PROPOSAL:"):
            if line_clean.upper().startswith(prefix):
                cmd_part = line_clean[len(prefix) :].strip()
                if (cmd_part.startswith("`") and cmd_part.endswith("`")) or (
                    cmd_part.startswith('"') and cmd_part.endswith('"')
                ):
                    cmd_part = cmd_part[1:-1].strip()
                try:
                    tokens = shlex.split(cmd_part)
                    if tokens:
                        return CommandRequest(program=tokens[0], args=tokens[1:])
                except Exception:
                    return None

    return None
