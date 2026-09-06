"""Filesystem inspection tools (strictly read-only)."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from avi.tools.base import BaseTool, ToolResult


def format_bytes(size_bytes: int | float | None) -> str:
    """Format byte counts into human-readable strings (e.g. 4.2 MB)."""
    if size_bytes is None:
        return "-"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} PB"


class ListDirectoryTool(BaseTool):
    """List files and subdirectories within a specified path."""

    name = "filesystem.list_directory"
    description = "List files and subdirectories within a directory path without recursion."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list (default: current directory).",
                "default": ".",
            }
        },
    }

    def execute(self, path: str = ".", **kwargs: Any) -> ToolResult:
        try:
            target_path = Path(path).resolve()
            if not target_path.exists():
                return ToolResult(success=False, error=f"Directory '{target_path}' does not exist.")
            if not target_path.is_dir():
                return ToolResult(success=False, error=f"Path '{target_path}' is not a directory.")

            entries: list[dict[str, Any]] = []
            with os.scandir(target_path) as it:
                for entry in it:
                    if len(entries) >= 100:
                        break
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                        stat_res = entry.stat(follow_symlinks=False)
                        size = None if is_directory else stat_res.st_size
                        entries.append(
                            {
                                "name": entry.name,
                                "type": "dir" if is_directory else "file",
                                "size_bytes": size,
                            }
                        )
                    except (OSError, PermissionError):
                        entries.append(
                            {
                                "name": entry.name,
                                "type": "unknown",
                                "size_bytes": None,
                            }
                        )
        except PermissionError:
            return ToolResult(
                success=False, error=f"Permission denied accessing directory '{path}'."
            )
        except OSError as err:
            return ToolResult(success=False, error=f"Error reading directory '{path}': {err}")

        # Sort directories first, then alphabetical
        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))

        # Human-readable formatting
        lines = [f"Contents of {target_path}:"]
        if not entries:
            lines.append("  (empty directory)")
        else:
            for item in entries:
                name_display = item["name"] + ("/" if item["type"] == "dir" else "")
                size_display = (
                    "[dir]" if item["type"] == "dir" else format_bytes(item["size_bytes"])
                )
                lines.append(f"  {name_display:<32} {size_display}")

        structured_data = {
            "path": str(target_path),
            "entries": entries,
            "total_entries": len(entries),
        }

        return ToolResult(
            success=True,
            data=structured_data,
            display_override="\n".join(lines),
        )


class FileMetadataTool(BaseTool):
    """Retrieve metadata about a specific file or path without reading contents."""

    name = "filesystem.file_metadata"
    description = "Retrieve metadata (size, permissions, timestamps) for a file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file or directory to inspect.",
            }
        },
        "required": ["path"],
    }

    def execute(self, path: str, **kwargs: Any) -> ToolResult:
        try:
            target_path = Path(path).resolve()
            if not target_path.exists():
                return ToolResult(success=False, error=f"File '{target_path}' does not exist.")

            stat_res = target_path.stat()
            mod_dt = datetime.fromtimestamp(stat_res.st_mtime, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            is_file = target_path.is_file()
            is_dir = target_path.is_dir()
            file_type = "file" if is_file else ("dir" if is_dir else "other")

            structured_data = {
                "path": str(target_path),
                "type": file_type,
                "size_bytes": stat_res.st_size,
                "modified": mod_dt,
                "permissions": oct(stat_res.st_mode & 0o777),
            }

            lines = [
                f"File: {target_path}",
                f"Type: {file_type}",
                f"Size: {format_bytes(stat_res.st_size)} ({stat_res.st_size} bytes)",
                f"Modified: {mod_dt}",
                f"Permissions: {oct(stat_res.st_mode & 0o777)}",
            ]

            return ToolResult(
                success=True,
                data=structured_data,
                display_override="\n".join(lines),
            )
        except PermissionError:
            return ToolResult(
                success=False, error=f"Permission denied accessing metadata for '{path}'."
            )
        except OSError as err:
            return ToolResult(success=False, error=f"Error inspecting file '{path}': {err}")
