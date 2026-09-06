"""Command executor subsystem for AVI.

Executes structured command requests with strict subprocess isolation, bounded outputs,
and configurable timeouts. Never uses shell=True.
"""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from avi.execution.models import (
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_MAX_OUTPUT_BYTES,
    CommandRequest,
    ExecutionResult,
)


class CommandExecutor:
    """Controlled, non-shell executor for proposed shell commands."""

    def __init__(
        self,
        default_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        default_max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self.default_timeout = default_timeout
        self.default_max_output_bytes = default_max_output_bytes

    def execute(self, request: CommandRequest) -> ExecutionResult:
        """Execute a CommandRequest using subprocess without shell expansion."""
        if not request.program or not request.program.strip():
            return ExecutionResult(
                command="",
                exit_code=1,
                stdout="",
                stderr="Empty command program",
                duration_ms=0.0,
                error="Empty command program",
            )

        program = request.program.strip()
        cmd_list = [program] + list(request.args)
        cwd = request.get_cwd()
        timeout = request.timeout if request.timeout > 0 else self.default_timeout
        max_bytes = (
            request.max_output_bytes
            if request.max_output_bytes > 0
            else self.default_max_output_bytes
        )

        # Check executable existence before spawning
        executable_path: str | None = None
        if "/" in program:
            p = Path(program)
            if not p.is_absolute():
                p = (cwd / p).resolve()
            if p.is_file():
                executable_path = str(p)
        else:
            executable_path = shutil.which(program)

        if executable_path is None:
            return ExecutionResult(
                command=request.command_line,
                exit_code=127,
                stdout="",
                stderr=f"Command not found: {program}",
                duration_ms=0.0,
                error=f"Command not found: {program}",
            )

        # Build clean environment
        env = request.env if request.env is not None else os.environ.copy()

        t0 = time.perf_counter()
        output_truncated = False

        try:
            # shell=False is strictly enforced!
            proc = subprocess.Popen(
                cmd_list,
                shell=False,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

            try:
                raw_stdout, raw_stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Cleanly terminate process group to avoid orphans
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()

                try:
                    raw_stdout, raw_stderr = proc.communicate(timeout=1.0)
                except Exception:
                    raw_stdout, raw_stderr = b"", b""

                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return ExecutionResult(
                    command=request.command_line,
                    exit_code=-1,
                    stdout=raw_stdout.decode("utf-8", errors="replace"),
                    stderr=f"Command timed out after {timeout:.1f}s",
                    duration_ms=elapsed_ms,
                    timed_out=True,
                    error=f"Command timed out after {timeout:.1f}s",
                )

            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            # Enforce output bounds
            if len(raw_stdout) > max_bytes:
                raw_stdout = raw_stdout[:max_bytes]
                stdout_str = (
                    raw_stdout.decode("utf-8", errors="replace")
                    + "\n[Output truncated: exceeded limit]"
                )
                output_truncated = True
            else:
                stdout_str = raw_stdout.decode("utf-8", errors="replace")

            if len(raw_stderr) > max_bytes:
                raw_stderr = raw_stderr[:max_bytes]
                stderr_str = (
                    raw_stderr.decode("utf-8", errors="replace")
                    + "\n[Output truncated: exceeded limit]"
                )
                output_truncated = True
            else:
                stderr_str = raw_stderr.decode("utf-8", errors="replace")

            return ExecutionResult(
                command=request.command_line,
                exit_code=proc.returncode,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_ms=elapsed_ms,
                timed_out=False,
                output_truncated=output_truncated,
                error=stderr_str.strip() if proc.returncode != 0 and stderr_str.strip() else None,
            )

        except PermissionError as pe:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                command=request.command_line,
                exit_code=126,
                stdout="",
                stderr=f"Permission denied: {pe}",
                duration_ms=elapsed_ms,
                error=f"Permission denied: {pe}",
            )
        except FileNotFoundError as fe:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                command=request.command_line,
                exit_code=127,
                stdout="",
                stderr=f"Command not found: {fe}",
                duration_ms=elapsed_ms,
                error=f"Command not found: {fe}",
            )
        except OSError as oe:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                command=request.command_line,
                exit_code=1,
                stdout="",
                stderr=f"OS execution error: {oe}",
                duration_ms=elapsed_ms,
                error=f"OS execution error: {oe}",
            )
