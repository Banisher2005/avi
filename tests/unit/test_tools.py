"""Unit tests for read-only tool subsystem."""

import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from avi.config import Config
from avi.core.router import Router
from avi.tools.base import BaseTool, ToolResult
from avi.tools.filesystem import FileMetadataTool, ListDirectoryTool, format_bytes
from avi.tools.git import GitBranchTool, GitLogTool, GitStatusTool
from avi.tools.registry import ToolRegistry, create_default_registry
from avi.tools.system import DiskUsageTool, ProcessesTool, SystemInfoTool


# =====================================================================
# 1. Formatters and ToolResult
# =====================================================================

def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1048576) == "1.0 MB"
    assert format_bytes(1073741824) == "1.0 GB"
    assert format_bytes(None) == "-"


def test_tool_result_format_display():
    res_ok = ToolResult(success=True, data={"key": "val"}, display_override="custom output")
    assert res_ok.format_display() == "custom output"

    res_err = ToolResult(success=False, error="Denied")
    assert "Error: Denied" in res_err.format_display()


# =====================================================================
# 2. Filesystem Tools
# =====================================================================

def test_list_directory_valid(tmp_path):
    (tmp_path / "file1.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()

    tool = ListDirectoryTool()
    res = tool.execute(path=str(tmp_path))

    assert res.success is True
    assert res.data["total_entries"] == 2
    entries = res.data["entries"]
    assert any(e["name"] == "file1.txt" and e["type"] == "file" for e in entries)
    assert any(e["name"] == "subdir" and e["type"] == "dir" for e in entries)
    assert "file1.txt" in res.format_display()
    assert "subdir/" in res.format_display()


def test_list_directory_empty(tmp_path):
    tool = ListDirectoryTool()
    res = tool.execute(path=str(tmp_path))

    assert res.success is True
    assert res.data["total_entries"] == 0
    assert "(empty directory)" in res.format_display()


def test_list_directory_nonexistent():
    tool = ListDirectoryTool()
    res = tool.execute(path="/nonexistent/path/for/sure/12345")
    assert res.success is False
    assert "does not exist" in res.error


def test_list_directory_not_a_dir(tmp_path):
    f = tmp_path / "single_file.txt"
    f.write_text("not a dir")

    tool = ListDirectoryTool()
    res = tool.execute(path=str(f))
    assert res.success is False
    assert "not a directory" in res.error


def test_list_directory_permission_error(tmp_path):
    tool = ListDirectoryTool()
    with patch("os.scandir", side_effect=PermissionError("Permission denied")):
        res = tool.execute(path=str(tmp_path))
        assert res.success is False
        assert "Permission denied" in res.error


def test_file_metadata_valid(tmp_path):
    test_file = tmp_path / "meta_test.txt"
    test_file.write_text("content 12345")

    tool = FileMetadataTool()
    res = tool.execute(path=str(test_file))

    assert res.success is True
    assert res.data["type"] == "file"
    assert res.data["size_bytes"] == len("content 12345")
    assert "Modified" in res.format_display()
    assert "Size:" in res.format_display()


def test_file_metadata_nonexistent():
    tool = FileMetadataTool()
    res = tool.execute(path="/nonexistent/file.txt")
    assert res.success is False
    assert "does not exist" in res.error


def test_file_metadata_permission_error(tmp_path):
    test_file = tmp_path / "locked.txt"
    test_file.write_text("secret")

    tool = FileMetadataTool()
    with patch("pathlib.Path.resolve", side_effect=PermissionError("Access denied")):
        res = tool.execute(path=str(test_file))
        assert res.success is False
        assert "Permission denied" in res.error


# =====================================================================
# 3. System Tools
# =====================================================================

def test_processes_tool_success():
    fake_ps_output = (
        "    PID COMMAND         %CPU %MEM\n"
        " 1001 python           5.0  2.5\n"
        " 1002 zsh              0.1  0.5\n"
    )
    tool = ProcessesTool()
    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = fake_ps_output
        mock_run.return_value = mock_proc

        res = tool.execute(limit=5, sort_by="memory")
        assert res.success is True
        assert len(res.data) == 2
        assert res.data[0]["name"] == "python"
        assert res.data[0]["memory_percent"] == 2.5
        assert "python" in res.format_display()

        # Security check: verify subprocess called with list of args, not shell=True
        call_args = mock_run.call_args
        assert isinstance(call_args[0][0], list)
        assert call_args[1].get("shell") is not True


def test_processes_tool_failure():
    tool = ProcessesTool()
    with patch("subprocess.run", side_effect=FileNotFoundError("ps not found")):
        res = tool.execute()
        assert res.success is False
        assert "Unable to read process table" in res.error


def test_disk_usage_tool(tmp_path):
    tool = DiskUsageTool()
    # Test with real disk_usage on tmp_path
    res = tool.execute(path=str(tmp_path))
    assert res.success is True
    assert "Total:" in res.format_display()
    assert "Available:" in res.format_display()
    assert res.data["total_bytes"] > 0
    assert res.data["free_bytes"] > 0


def test_disk_usage_tool_invalid_path():
    tool = DiskUsageTool()
    with patch("shutil.disk_usage", side_effect=OSError("Invalid mount")):
        res = tool.execute(path="/nonexistent/mount")
        assert res.success is False
        assert "Could not inspect disk usage" in res.error


def test_system_info_tool():
    tool = SystemInfoTool()
    res = tool.execute()
    assert res.success is True
    assert "os" in res.data
    assert "kernel" in res.data
    assert "cpu_count" in res.data
    assert res.data["cpu_count"] >= 1
    # Security check: ensure no env vars or passwords leaked
    assert "PATH" not in res.data
    assert "password" not in str(res.data).lower()


# =====================================================================
# 4. Git Tools
# =====================================================================

def test_git_status_tool_repo(tmp_path):
    tool = GitStatusTool()
    fake_ctx = MagicMock()
    fake_ctx.is_repo = True
    fake_ctx.root = str(tmp_path)
    fake_ctx.branch = "feature/test"
    fake_ctx.is_dirty = False
    fake_ctx.modified_count = 0
    fake_ctx.untracked_count = 0

    with patch("avi.tools.git.collect_git_context", return_value=fake_ctx):
        res = tool.execute(path=str(tmp_path))
        assert res.success is True
        assert res.data["branch"] == "feature/test"
        assert res.data["is_dirty"] is False
        assert "Status: clean" in res.format_display()


def test_git_status_tool_non_repo(tmp_path):
    tool = GitStatusTool()
    fake_ctx = MagicMock()
    fake_ctx.is_repo = False

    with patch("avi.tools.git.collect_git_context", return_value=fake_ctx):
        res = tool.execute(path=str(tmp_path))
        assert res.success is True
        assert res.data["is_repo"] is False
        assert "Not in a Git repository." in res.format_display()


def test_git_branch_tool_repo():
    tool = GitBranchTool()
    fake_ctx = MagicMock()
    fake_ctx.is_repo = True
    fake_ctx.branch = "main"

    with patch("avi.tools.git.collect_git_context", return_value=fake_ctx):
        res = tool.execute()
        assert res.success is True
        assert res.data["branch"] == "main"
        assert res.format_display() == "main"


def test_git_log_tool_success(tmp_path):
    tool = GitLogTool()
    fake_ctx = MagicMock()
    fake_ctx.is_repo = True
    fake_ctx.branch = "main"
    fake_ctx.root = str(tmp_path)

    fake_log_stdout = "abc1234|Alice|2026-09-06|Initial commit\n"

    with patch("avi.tools.git.collect_git_context", return_value=fake_ctx):
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = fake_log_stdout
            mock_run.return_value = mock_proc

            res = tool.execute(limit=1, path=str(tmp_path))
            assert res.success is True
            assert len(res.data) == 1
            assert res.data[0]["hash"] == "abc1234"
            assert res.data[0]["author"] == "Alice"
            assert res.data[0]["message"] == "Initial commit"
            assert "abc1234 (2026-09-06) Initial commit" in res.format_display()


def test_git_log_tool_non_repo():
    tool = GitLogTool()
    fake_ctx = MagicMock()
    fake_ctx.is_repo = False

    with patch("avi.tools.git.collect_git_context", return_value=fake_ctx):
        res = tool.execute()
        assert res.success is False
        assert "Not in a Git repository." in res.format_display()


# =====================================================================
# 5. Registry Tests
# =====================================================================

def test_registry_registration_and_lookup():
    reg = ToolRegistry()
    dummy_tool = ListDirectoryTool()
    reg.register(dummy_tool)

    assert "filesystem.list_directory" in reg
    assert reg.get("filesystem.list_directory") is dummy_tool
    assert reg.get("nonexistent.tool") is None
    assert len(reg) == 1
    assert "filesystem.list_directory" in reg.list_names()


def test_default_registry_contains_all_8_tools():
    reg = create_default_registry()
    assert len(reg) == 8
    expected_names = [
        "filesystem.list_directory",
        "filesystem.file_metadata",
        "system.processes",
        "system.disk_usage",
        "system.system_info",
        "git.status",
        "git.branch",
        "git.log",
    ]
    for name in expected_names:
        assert name in reg, f"Missing tool: {name}"


# =====================================================================
# 6. Router Deterministic Tool Selection & Security
# =====================================================================

def test_router_tool_selection():
    config = Config.load()
    router = Router(config)

    # Tool: list_directory
    res_files = router.check_fast_path("what files are here?")
    assert res_files is not None
    assert "Contents of" in res_files

    # Tool: processes
    res_ram = router.check_fast_path("what's using the most RAM?")
    assert res_ram is not None
    assert "Top processes" in res_ram

    # Tool: disk_usage
    res_disk = router.check_fast_path("how much disk space do I have?")
    assert res_disk is not None
    assert "Disk Usage" in res_disk

    # Tool: git_log
    res_log = router.check_fast_path("recent commits")
    assert res_log is not None
    assert "Recent commits" in res_log

    # Tool: git_status
    res_status = router.check_fast_path("git status")
    assert res_status is not None
    assert "Git Repository" in res_status

    # Command questions bypass tools and delegate to LLM
    assert router.check_fast_path("what command lists files?") is None
    assert router.check_fast_path("what command shows disk usage?") is None
    assert router.check_fast_path("what command shows RAM usage?") is None


def test_security_read_only_guarantee():
    reg = create_default_registry()
    for tool in reg:
        assert tool.safety_level == "read_only"
        # Verify no write, delete, or modify in tool methods
        assert not hasattr(tool, "delete")
        assert not hasattr(tool, "modify")
        assert not hasattr(tool, "write")
