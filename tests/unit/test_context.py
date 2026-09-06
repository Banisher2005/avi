"""Unit tests for the context awareness subsystem."""

import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from avi.config import Config
from avi.context.collectors import (
    collect_git_context,
    collect_previous_command_context,
    collect_snapshot,
    collect_terminal_context,
    detect_needed_context,
)
from avi.context.models import (
    ContextSnapshot,
    GitContext,
    PreviousCommandContext,
    TerminalContext,
)
from avi.core.router import Router


def test_terminal_context_model():
    ctx = TerminalContext(cwd="/home/user/proj", os_name="Linux", shell="zsh")
    text = ctx.format_text()
    assert "cwd=/home/user/proj" in text
    assert "shell=zsh" in text
    assert "os=Linux" in text


def test_git_context_model_clean():
    ctx = GitContext(is_repo=True, root="/home/user/proj", branch="main", is_dirty=False)
    text = ctx.format_text()
    assert "[git]" in text
    assert "branch=main" in text
    assert "status=clean" in text


def test_git_context_model_dirty():
    ctx = GitContext(
        is_repo=True,
        root="/home/user/proj",
        branch="feature",
        is_dirty=True,
        modified_count=2,
        untracked_count=1,
    )
    text = ctx.format_text()
    assert "status=dirty" in text
    assert "modified_files=2" in text
    assert "untracked_files=1" in text


def test_git_context_model_non_repo():
    ctx = GitContext(is_repo=False)
    assert ctx.format_text() == "[git]\nis_repo=false"


def test_previous_command_context_model():
    ctx = PreviousCommandContext(command="pytest", exit_code=1, output="Failing test")
    text = ctx.format_text()
    assert "[previous_command]" in text
    assert "command=pytest" in text
    assert "exit_code=1" in text
    assert "output=Failing test" in text


def test_previous_command_truncates_long_output():
    long_out = "a" * 1000
    ctx = PreviousCommandContext(command="cat bigfile", exit_code=0, output=long_out)
    text = ctx.format_text()
    assert "[truncated]" in text
    assert len(text) < 600


def test_context_snapshot_serialization():
    term = TerminalContext(cwd="/app", os_name="Linux", shell="bash")
    git = GitContext(is_repo=True, root="/app", branch="main")
    snapshot = ContextSnapshot(terminal=term, git=git)

    assert not snapshot.is_empty()
    prompt_ctx = snapshot.to_prompt_context()
    assert "[terminal]" in prompt_ctx
    assert "[git]" in prompt_ctx
    assert "[previous_command]" not in prompt_ctx


def test_collect_terminal_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/custom_shell")
    ctx = collect_terminal_context(tmp_path)
    assert ctx.cwd == str(tmp_path.resolve())
    assert ctx.shell == "custom_shell"
    assert ctx.os_name != ""


def test_collect_git_context_clean_repo(tmp_path):
    def mock_subprocess_run(cmd, *args, **kwargs):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        if "--is-inside-work-tree" in cmd:
            mock_proc.stdout = "true\n"
        elif "--show-toplevel" in cmd:
            mock_proc.stdout = str(tmp_path) + "\n"
        elif "--show-current" in cmd:
            mock_proc.stdout = "feature/test\n"
        elif "status" in cmd:
            mock_proc.stdout = ""  # Clean status
        return mock_proc

    with patch("subprocess.run", side_effect=mock_subprocess_run):
        ctx = collect_git_context(tmp_path)
        assert ctx.is_repo is True
        assert ctx.root == str(tmp_path)
        assert ctx.branch == "feature/test"
        assert ctx.is_dirty is False


def test_collect_git_context_dirty_repo(tmp_path):
    def mock_subprocess_run(cmd, *args, **kwargs):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        if "--is-inside-work-tree" in cmd:
            mock_proc.stdout = "true\n"
        elif "--show-toplevel" in cmd:
            mock_proc.stdout = str(tmp_path) + "\n"
        elif "--show-current" in cmd:
            mock_proc.stdout = "main\n"
        elif "status" in cmd:
            mock_proc.stdout = " M file1.py\n?? new_file.txt\n"
        return mock_proc

    with patch("subprocess.run", side_effect=mock_subprocess_run):
        ctx = collect_git_context(tmp_path)
        assert ctx.is_repo is True
        assert ctx.is_dirty is True
        assert ctx.modified_count == 1
        assert ctx.untracked_count == 1


def test_collect_git_context_non_git_dir(tmp_path):
    def mock_subprocess_run(cmd, *args, **kwargs):
        mock_proc = MagicMock()
        mock_proc.returncode = 128
        mock_proc.stdout = ""
        mock_proc.stderr = "fatal: not a git repository\n"
        return mock_proc

    with patch("subprocess.run", side_effect=mock_subprocess_run):
        ctx = collect_git_context(tmp_path)
        assert ctx.is_repo is False
        assert ctx.branch is None


def test_collect_previous_command_env(monkeypatch):
    monkeypatch.setenv("AVI_PREV_CMD", "npm test")
    monkeypatch.setenv("AVI_PREV_EXIT_CODE", "1")
    monkeypatch.setenv("AVI_PREV_OUTPUT", "1 failed")

    ctx = collect_previous_command_context()
    assert ctx is not None
    assert ctx.command == "npm test"
    assert ctx.exit_code == 1
    assert ctx.output == "1 failed"


def test_collect_previous_command_file(tmp_path, monkeypatch):
    fake_file = tmp_path / "last_command.json"
    fake_file.write_text(json.dumps({
        "command": "cargo build",
        "exit_code": 101,
        "output": "compilation error"
    }))

    with patch("avi.context.collectors.LAST_COMMAND_FILE", fake_file):
        ctx = collect_previous_command_context()
        assert ctx is not None
        assert ctx.command == "cargo build"
        assert ctx.exit_code == 101


def test_collect_previous_command_none(monkeypatch):
    monkeypatch.delenv("AVI_PREV_CMD", raising=False)
    with patch("pathlib.Path.is_file", return_value=False):
        assert collect_previous_command_context() is None


def test_detect_needed_context_lazy_selection():
    # 1. Generic question -> NO context needed
    assert detect_needed_context("what command lists files?") == set()
    assert detect_needed_context("find files larger than 500MB") == set()
    assert detect_needed_context("how to unpack tar.gz") == set()

    # 2. Git query -> git context needed
    assert "git" in detect_needed_context("summarize git status")
    assert "git" in detect_needed_context("what branch is this?")

    # 3. Terminal query -> terminal context needed
    assert "terminal" in detect_needed_context("what directory is this?")
    assert "terminal" in detect_needed_context("where am I?")

    # 4. Previous command query -> previous_command context needed
    assert "previous_command" in detect_needed_context("why did my last command fail?")
    assert "previous_command" in detect_needed_context("explain the previous command error")


def test_collect_snapshot_respects_needed():
    # Only git requested
    snap_git = collect_snapshot({"git"})
    assert snap_git.git is not None
    assert snap_git.terminal is None
    assert snap_git.previous_command is None

    # Empty requested
    snap_empty = collect_snapshot(set())
    assert snap_empty.is_empty()


def test_router_deterministic_fast_path(tmp_path):
    config = Config.load()
    router = Router(config)

    # Fast path: current directory
    assert router.check_fast_path("what directory am I in?") == str(Path.cwd())
    assert router.check_fast_path("where am I") == str(Path.cwd())
    assert router.check_fast_path("pwd") == str(Path.cwd())

    # Fast path: shell
    shell = router.check_fast_path("what shell am I using?")
    assert shell is not None and len(shell) > 0

    # Fast path: OS
    os_name = router.check_fast_path("what OS is this?")
    assert os_name is not None and len(os_name) > 0

    # Non-fast-path query returns None
    assert router.check_fast_path("how to install nginx") is None
