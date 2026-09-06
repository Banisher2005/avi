"""Natural-language response synthesis for tool results and assistant queries."""

from typing import Any

from avi.tools.base import ToolResult


def format_disk_space_conversational(result: ToolResult) -> str:
    """Format structured disk usage data into a conversational sentence.

    Example: 'You have about 154 GB free out of 240 GB on your main drive.'
    """
    if not result.success or not isinstance(result.data, dict):
        return result.format_display()

    data = result.data
    free_bytes = data.get("free_bytes", 0)
    total_bytes = data.get("total_bytes", 0)
    path = data.get("path", "/")

    # Convert to Gigabytes
    free_gb = free_bytes / (1024**3)
    total_gb = total_bytes / (1024**3)

    # Format numbers nicely
    if free_gb >= 10:
        free_str = f"{int(round(free_gb))} GB"
    else:
        free_str = f"{free_gb:.1f} GB"

    if total_gb >= 10:
        total_str = f"{int(round(total_gb))} GB"
    else:
        total_str = f"{total_gb:.1f} GB"

    drive_label = "your main drive" if path == "/" else f"drive ({path})"
    return f"You have about {free_str} free out of {total_str} on {drive_label}."


def get_memory_summary_conversational() -> str:
    """Read Linux /proc/meminfo and synthesize a concise conversational RAM summary.

    Example: 'You have about 9.1 GB of RAM available out of 14.5 GB total (37% in use).'
    """
    try:
        mem: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = int(parts[1].split()[0])

        if "MemTotal" in mem and "MemAvailable" in mem:
            total_gb = mem["MemTotal"] / (1024 * 1024)
            avail_gb = mem["MemAvailable"] / (1024 * 1024)
            used_gb = total_gb - avail_gb
            pct = (used_gb / total_gb) * 100.0 if total_gb > 0 else 0.0
            return (
                f"You have about {avail_gb:.1f} GB of RAM available out of {total_gb:.1f} GB total "
                f"({pct:.0f}% in use)."
            )
    except Exception:
        pass
    return "Memory information is currently unavailable."


def format_processes_conversational(result: ToolResult, sort_by: str = "memory") -> str:
    """Format process metrics into a concise natural language explanation.

    Example: 'llama-server is currently using the most memory at about 7.6%, followed by Chrome.'
    """
    if not result.success or not isinstance(result.data, list) or not result.data:
        return result.format_display()

    rows = result.data
    metric_key = "memory_percent" if sort_by == "memory" else "cpu_percent"
    metric_label = "memory" if sort_by == "memory" else "CPU"

    top = rows[0]
    top_name = top.get("name", "Unknown process")
    top_val = top.get(metric_key, 0.0)

    if len(rows) > 1:
        second = rows[1]
        second_name = second.get("name", "")
        if second_name and second_name.lower() != top_name.lower():
            return (
                f"{top_name} is currently using the most {metric_label} at about "
                f"{top_val:.1f}%, followed by {second_name}."
            )

    return f"{top_name} is currently using the most {metric_label} at about {top_val:.1f}%."


def format_system_info_conversational(result: ToolResult) -> str:
    """Format basic system info into a conversational summary."""
    if not result.success or not isinstance(result.data, dict):
        return result.format_display()

    info = result.data
    os_name = info.get("os", "Linux")
    kernel = info.get("kernel", "")
    arch = info.get("architecture", "")
    cpus = info.get("cpu_count", 1)

    kernel_part = f" ({kernel})" if kernel else ""
    return f"You are running {os_name}{kernel_part} on {arch} with {cpus} CPU cores."


def format_git_status_conversational(result: ToolResult) -> str:
    """Format git repository status into a conversational summary."""
    if not result.success or not isinstance(result.data, dict):
        return result.format_display()

    data = result.data
    branch = data.get("branch") or "main"
    if data.get("clean"):
        return f"On git branch '{branch}'. Working directory is clean."

    staged = len(data.get("staged", []))
    modified = len(data.get("modified", []))
    untracked = len(data.get("untracked", []))

    parts = []
    if staged:
        parts.append(f"{staged} staged")
    if modified:
        parts.append(f"{modified} modified")
    if untracked:
        parts.append(f"{untracked} untracked")

    details = ", ".join(parts) if parts else "uncommitted changes"
    return f"On git branch '{branch}' with {details}."
