"""System inspection tools (strictly read-only)."""

import os
import platform
import shutil
import subprocess
from typing import Any

from avi.tools.base import BaseTool, ToolResult
from avi.tools.filesystem import format_bytes


class ProcessesTool(BaseTool):
    """Retrieve top running processes by CPU or memory usage."""

    name = "system.processes"
    description = "Inspect top running processes sorted by memory or CPU."
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of processes to return (default: 10, max: 50).",
                "default": 10,
            },
            "sort_by": {
                "type": "string",
                "description": "Sort metric: 'memory' or 'cpu'.",
                "enum": ["memory", "cpu"],
                "default": "memory",
            },
        },
    }

    def execute(self, limit: int = 10, sort_by: str = "memory", **kwargs: Any) -> ToolResult:
        limit = min(max(1, int(limit)), 50)
        sort_flag = "--sort=-%mem" if sort_by.lower() == "memory" else "--sort=-%cpu"

        try:
            # Execute ps with strict array arguments, never shell=True
            proc = subprocess.run(
                ["ps", "-eo", "pid,comm,%cpu,%mem", sort_flag],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=1.5,
            )
            if proc.returncode != 0:
                return ToolResult(
                    success=False, error=f"ps execution failed: {proc.stderr.strip()}"
                )

            lines = proc.stdout.strip().splitlines()
            if len(lines) <= 1:
                return ToolResult(
                    success=True, data=[], display_override="No process information returned."
                )

            # Skip header line, process rows
            process_rows: list[dict[str, Any]] = []

            for line in lines[1 : limit + 1]:
                parts = line.strip().split(None, 3)
                if len(parts) >= 4:
                    try:
                        pid = int(parts[0])
                        comm = parts[1]
                        cpu = float(parts[2])
                        mem = float(parts[3])
                        process_rows.append(
                            {
                                "pid": pid,
                                "name": comm,
                                "cpu_percent": cpu,
                                "memory_percent": mem,
                            }
                        )
                    except ValueError:
                        continue

            header_str = f"Top processes by {sort_by}:"
            table_lines = [header_str, f"  {'PID':>8}  {'NAME':<20} {'%MEM':>6}  {'%CPU':>6}"]
            for p in process_rows:
                table_lines.append(
                    f"  {p['pid']:>8d}  {p['name']:<20} {p['memory_percent']:>5.1f}%  {p['cpu_percent']:>5.1f}%"
                )

            return ToolResult(
                success=True,
                data=process_rows,
                display_override="\n".join(table_lines),
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as err:
            return ToolResult(success=False, error=f"Unable to read process table: {err}")


class DiskUsageTool(BaseTool):
    """Retrieve filesystem disk space metrics via standard library shutil."""

    name = "system.disk_usage"
    description = "Check available, used, and total disk space on a filesystem path."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Filesystem path or mount point to inspect (default: '/').",
                "default": "/",
            }
        },
    }

    def execute(self, path: str = "/", **kwargs: Any) -> ToolResult:
        try:
            resolved_path = os.path.abspath(path)
            usage = shutil.disk_usage(resolved_path)

            total = usage.total
            used = usage.used
            free = usage.free
            pct_used = (used / total * 100.0) if total > 0 else 0.0

            structured_data = {
                "path": resolved_path,
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "percent_used": round(pct_used, 1),
            }

            lines = [
                f"Disk Usage ({resolved_path}):",
                f"  Total:     {format_bytes(total)}",
                f"  Used:      {format_bytes(used)} ({pct_used:.1f}%)",
                f"  Available: {format_bytes(free)}",
            ]

            return ToolResult(
                success=True,
                data=structured_data,
                display_override="\n".join(lines),
            )
        except (OSError, ValueError) as err:
            return ToolResult(
                success=False, error=f"Could not inspect disk usage for '{path}': {err}"
            )


class SystemInfoTool(BaseTool):
    """Retrieve basic non-sensitive machine and operating system details."""

    name = "system.system_info"
    description = "Retrieve basic operating system, kernel, CPU, and machine architecture."
    input_schema = {
        "type": "object",
        "properties": {},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            info = {
                "os": platform.system() or "Linux",
                "kernel": platform.release(),
                "architecture": platform.machine(),
                "hostname": platform.node(),
                "cpu_count": os.cpu_count() or 1,
            }

            lines = [
                "System Information:",
                f"  OS:           {info['os']}",
                f"  Kernel:       {info['kernel']}",
                f"  Architecture: {info['architecture']}",
                f"  CPU Count:    {info['cpu_count']}",
                f"  Hostname:     {info['hostname']}",
            ]

            return ToolResult(
                success=True,
                data=info,
                display_override="\n".join(lines),
            )
        except Exception as err:
            return ToolResult(success=False, error=f"Could not collect system information: {err}")
