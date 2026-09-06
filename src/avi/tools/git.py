"""Git inspection tools (strictly read-only)."""

import os
from pathlib import Path
import subprocess
from typing import Any

from avi.context.collectors import collect_git_context
from avi.tools.base import BaseTool, ToolResult


class GitStatusTool(BaseTool):
    """Retrieve structured Git repository status."""

    name = "git.status"
    description = "Inspect git branch and clean/dirty working directory status."

    def execute(self, path: str = ".", **kwargs: Any) -> ToolResult:
        ctx = collect_git_context(path)
        if not ctx.is_repo:
            return ToolResult(
                success=True,
                data={"is_repo": False},
                display_override="Not in a Git repository.",
            )

        structured_data = {
            "is_repo": True,
            "root": ctx.root,
            "branch": ctx.branch,
            "is_dirty": ctx.is_dirty,
            "modified_count": ctx.modified_count,
            "untracked_count": ctx.untracked_count,
        }

        status_label = "dirty" if ctx.is_dirty else "clean"
        lines = [
            f"Git Repository: {ctx.root or path}",
            f"  Branch: {ctx.branch}",
            f"  Status: {status_label}",
        ]
        if ctx.modified_count > 0:
            lines.append(f"  Modified files:  {ctx.modified_count}")
        if ctx.untracked_count > 0:
            lines.append(f"  Untracked files: {ctx.untracked_count}")

        return ToolResult(
            success=True,
            data=structured_data,
            display_override="\n".join(lines),
        )


class GitBranchTool(BaseTool):
    """Retrieve current Git branch name."""

    name = "git.branch"
    description = "Get the active Git branch for the repository."

    def execute(self, path: str = ".", **kwargs: Any) -> ToolResult:
        ctx = collect_git_context(path)
        if not ctx.is_repo:
            return ToolResult(
                success=True,
                data={"is_repo": False, "branch": None},
                display_override="Not in a Git repository.",
            )

        branch_name = ctx.branch or "HEAD"
        return ToolResult(
            success=True,
            data={"is_repo": True, "branch": branch_name},
            display_override=branch_name,
        )


class GitLogTool(BaseTool):
    """Retrieve recent commits from the repository (read-only)."""

    name = "git.log"
    description = "View recent Git commit history without full diffs."

    def execute(self, limit: int = 5, path: str = ".", **kwargs: Any) -> ToolResult:
        ctx = collect_git_context(path)
        if not ctx.is_repo:
            return ToolResult(
                success=False,
                error="Not in a Git repository.",
                display_override="Not in a Git repository.",
            )

        limit = min(max(1, int(limit)), 20)
        target_dir = ctx.root or str(Path(path).resolve())

        try:
            # Strictly constrained argument array, never shell=True
            proc = subprocess.run(
                ["git", "log", f"-n{limit}", "--pretty=format:%h|%an|%ad|%s", "--date=short"],
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=2.0,
            )
            if proc.returncode != 0:
                return ToolResult(success=False, error=f"git log failed: {proc.stderr.strip()}")

            lines = proc.stdout.strip().splitlines()
            commits: list[dict[str, str]] = []
            display_lines = [f"Recent commits ({ctx.branch or 'HEAD'}):"]

            for line in lines:
                parts = line.split("|", 3)
                if len(parts) == 4:
                    commit_info = {
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                    }
                    commits.append(commit_info)
                    display_lines.append(f"  {parts[0]} ({parts[2]}) {parts[3]} [{parts[1]}]")

            return ToolResult(
                success=True,
                data=commits,
                display_override="\n".join(display_lines),
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as err:
            return ToolResult(success=False, error=f"Failed to execute git log: {err}")
