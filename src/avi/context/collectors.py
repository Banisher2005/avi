"""Collectors for terminal, git, and previous command context."""

import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Set

from avi.context.models import (
    ContextSnapshot,
    GitContext,
    PreviousCommandContext,
    TerminalContext,
)

LAST_COMMAND_FILE = Path.home() / ".local" / "share" / "avi" / "last_command.json"


def collect_terminal_context(cwd: str | Path | None = None) -> TerminalContext:
    """Collect current working directory, operating system, and shell."""
    resolved_cwd = str(Path(cwd).resolve()) if cwd else os.getcwd()
    os_name = platform.system() or "Linux"

    # Shell detection: check $SHELL environment variable first
    raw_shell = os.environ.get("SHELL", "")
    if raw_shell:
        shell = Path(raw_shell).name
    else:
        shell = "bash"

    return TerminalContext(cwd=resolved_cwd, os_name=os_name, shell=shell)


def collect_git_context(cwd: str | Path | None = None) -> GitContext:
    """Collect minimal git repository status without reading full diffs."""
    target_dir = str(Path(cwd).resolve()) if cwd else os.getcwd()

    # 1. Quick check: check if inside git work tree
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1.0,
            check=False,
        )
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            return GitContext(is_repo=False)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return GitContext(is_repo=False)

    # 2. Get repository root
    root = None
    try:
        proc_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1.0,
            check=False,
        )
        if proc_root.returncode == 0:
            root = proc_root.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    # 3. Get current branch name
    branch = None
    try:
        proc_branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1.0,
            check=False,
        )
        if proc_branch.returncode == 0:
            branch = proc_branch.stdout.strip()
        if not branch:
            # Fallback for detached HEAD: short commit hash
            proc_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1.0,
                check=False,
            )
            if proc_hash.returncode == 0:
                branch = f"HEAD ({proc_hash.stdout.strip()})"
    except (subprocess.SubprocessError, OSError):
        pass

    # 4. Get clean/dirty status from porcelain
    is_dirty = False
    untracked_count = 0
    modified_count = 0

    try:
        proc_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1.0,
            check=False,
        )
        if proc_status.returncode == 0:
            lines = proc_status.stdout.splitlines()
            if lines:
                is_dirty = True
                for line in lines:
                    if line.startswith("??"):
                        untracked_count += 1
                    else:
                        modified_count += 1
    except (subprocess.SubprocessError, OSError):
        pass

    return GitContext(
        is_repo=True,
        root=root,
        branch=branch,
        is_dirty=is_dirty,
        untracked_count=untracked_count,
        modified_count=modified_count,
    )


def collect_previous_command_context() -> PreviousCommandContext | None:
    """Collect previous command information if available via env vars or last_command file."""
    # 1. Check environment variables
    env_cmd = os.environ.get("AVI_PREV_CMD")
    if env_cmd:
        exit_code_str = os.environ.get("AVI_PREV_EXIT_CODE")
        exit_code = int(exit_code_str) if exit_code_str and exit_code_str.isdigit() else None
        output = os.environ.get("AVI_PREV_OUTPUT")
        return PreviousCommandContext(command=env_cmd, exit_code=exit_code, output=output)

    # 2. Check last_command.json integration file
    if LAST_COMMAND_FILE.is_file():
        try:
            with open(LAST_COMMAND_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "command" in data:
                    return PreviousCommandContext(
                        command=str(data["command"]),
                        exit_code=data.get("exit_code"),
                        output=data.get("output"),
                    )
        except (json.JSONDecodeError, OSError):
            pass

    return None


# Keywords indicating specific context requirements
_GIT_KEYWORDS = {
    "git",
    "branch",
    "branches",
    "commit",
    "commits",
    "repo",
    "repository",
    "diff",
    "staged",
    "stash",
    "merge",
    "rebase",
    "remote",
}

_TERMINAL_KEYWORDS = {
    "directory",
    "folder",
    "path",
    "cwd",
    "pwd",
    "where am i",
    "shell",
    "zsh",
    "bash",
    "fish",
    "terminal",
    "os",
    "linux",
    "operating system",
    "distro",
}

_PREV_CMD_KEYWORDS = {
    "last command",
    "previous command",
    "why did it fail",
    "why did my last command fail",
    "command fail",
    "explain this error",
    "exit code",
}


def detect_needed_context(prompt: str) -> Set[str]:
    """Analyze prompt to determine which context sources (if any) are required."""
    prompt_lower = prompt.lower()
    tokens = set(re.findall(r"\b\w+\b", prompt_lower))
    needed: set[str] = set()

    # Check previous command phrases
    for phrase in _PREV_CMD_KEYWORDS:
        if phrase in prompt_lower:
            needed.add("previous_command")
            break

    # Check git keywords
    if tokens & _GIT_KEYWORDS:
        needed.add("git")

    # Check terminal keywords
    if (tokens & _TERMINAL_KEYWORDS) or ("where am i" in prompt_lower):
        needed.add("terminal")

    return needed


def collect_snapshot(needed: Set[str], cwd: str | Path | None = None) -> ContextSnapshot:
    """Collect only the explicitly requested context components."""
    terminal_ctx = collect_terminal_context(cwd) if "terminal" in needed else None
    git_ctx = collect_git_context(cwd) if "git" in needed else None
    prev_cmd_ctx = collect_previous_command_context() if "previous_command" in needed else None

    return ContextSnapshot(
        terminal=terminal_ctx,
        git=git_ctx,
        previous_command=prev_cmd_ctx,
    )
