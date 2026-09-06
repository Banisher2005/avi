"""Unit tests for CommandExecutor and execution subsystem."""

import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from avi.execution.errors import (
    CommandNotFoundError,
    CommandTimeoutError,
    ExecutionError,
    InvalidCommandError,
    PermissionDeniedError,
)
from avi.execution.executor import CommandExecutor
from avi.execution.models import CommandRequest, ExecutionResult, extract_command_proposal


def test_command_request_basic():
    req = CommandRequest(program="ls", args=["-l", "-a"])
    assert req.command_line == "ls -l -a"
    assert req.program == "ls"
    assert req.args == ["-l", "-a"]
    assert req.get_cwd() == Path.cwd().resolve()


def test_command_request_custom_cwd(tmp_path):
    req = CommandRequest(program="pwd", cwd=tmp_path)
    assert req.get_cwd() == tmp_path.resolve()


def test_execution_result_formatting():
    res = ExecutionResult(
        command="echo hello",
        exit_code=0,
        stdout="hello\n",
        stderr="",
        duration_ms=5.0,
    )
    assert res.success is True
    assert res.format_display() == "hello"


def test_execution_result_error_formatting():
    res = ExecutionResult(
        command="false",
        exit_code=1,
        stdout="",
        stderr="something failed",
        duration_ms=4.0,
    )
    assert res.success is False
    assert "something failed" in res.format_display()


def test_execute_echo():
    executor = CommandExecutor()
    req = CommandRequest(program="echo", args=["hello", "safe", "world"])
    res = executor.execute(req)
    assert res.success is True
    assert res.exit_code == 0
    assert res.stdout.strip() == "hello safe world"
    assert res.duration_ms > 0


def test_execute_empty_program():
    executor = CommandExecutor()
    req = CommandRequest(program="")
    res = executor.execute(req)
    assert res.success is False
    assert res.exit_code == 1
    assert "Empty command" in res.stderr


def test_execute_nonexistent_executable():
    executor = CommandExecutor()
    req = CommandRequest(program="nonexistent_binary_xyz_123")
    res = executor.execute(req)
    assert res.success is False
    assert res.exit_code == 127
    assert "Command not found" in res.stderr


def test_execute_timeout():
    executor = CommandExecutor(default_timeout=0.3)
    req = CommandRequest(
        program=sys.executable,
        args=["-c", "import time; time.sleep(2)"],
        timeout=0.2,
    )
    t0 = time.perf_counter()
    res = executor.execute(req)
    elapsed = time.perf_counter() - t0

    assert res.timed_out is True
    assert res.exit_code == -1
    assert "timed out" in res.stderr.lower()
    assert elapsed < 1.5


def test_execute_output_limit():
    executor = CommandExecutor(default_max_output_bytes=50)
    req = CommandRequest(
        program=sys.executable,
        args=["-c", "print('A' * 500)"],
        max_output_bytes=50,
    )
    res = executor.execute(req)
    assert res.success is True
    assert res.output_truncated is True
    assert "Output truncated" in res.stdout
    assert len(res.stdout.splitlines()[0]) == 50


def test_execute_permission_error(tmp_path):
    unexecutable = tmp_path / "script.sh"
    unexecutable.write_text("#!/bin/sh\necho hi\n")
    unexecutable.chmod(stat.S_IRUSR | stat.S_IWUSR)

    executor = CommandExecutor()
    req = CommandRequest(program=str(unexecutable))
    res = executor.execute(req)
    assert res.success is False
    assert res.exit_code in (126, 127)
    assert "Permission denied" in res.stderr or "Command not found" in res.stderr


def test_executor_never_uses_shell_true():
    executor = CommandExecutor()
    req = CommandRequest(program="echo", args=["test"])

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"test\n", b"")
        mock_proc.returncode = 0
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        executor.execute(req)

        assert mock_popen.called
        kwargs = mock_popen.call_args[1]
        assert kwargs.get("shell") is False


def test_extract_command_proposal_json():
    text = '```json\n{\n  "action": "execute",\n  "command": "ls -la"\n}\n```'
    req = extract_command_proposal(text)
    assert req is not None
    assert req.program == "ls"
    assert req.args == ["-la"]


def test_extract_command_proposal_command_tag():
    text = "COMMAND: rm test.tmp"
    req = extract_command_proposal(text)
    assert req is not None
    assert req.program == "rm"
    assert req.args == ["test.tmp"]


def test_extract_command_proposal_proposal_tag():
    text = "PROPOSAL: git checkout -b feature"
    req = extract_command_proposal(text)
    assert req is not None
    assert req.program == "git"
    assert req.args == ["checkout", "-b", "feature"]


def test_extract_command_proposal_natural_language():
    text = "To remove a file in Linux, use the `rm` command like: rm filename"
    req = extract_command_proposal(text)
    assert req is None
