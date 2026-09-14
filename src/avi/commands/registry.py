"""Extensible Command Registry that sits above the Capability Registry, Application Registry, and system utilities."""

from __future__ import annotations

import ast
import logging
import operator
import os
import re
import shutil
from pathlib import Path

from avi.apps.registry import ApplicationEntry, ApplicationRegistry
from avi.capabilities.models import BaseCapability
from avi.capabilities.registry import CapabilityRegistry, create_default_capability_registry
from avi.commands.models import (
    ActionResult,
    CommandCategory,
    CommandDefinition,
)

logger = logging.getLogger("avi.commands.registry")

# Allowed operators for zero-eval AST math evaluator
_SAFE_MATH_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def evaluate_safe_arithmetic(expression: str) -> str:
    """Safely evaluate arithmetic expressions in <1ms using AST traversal with ZERO eval() or exec()."""
    expr = expression.strip()
    # Strip common leading question / command keywords
    expr = re.sub(
        r"^(?:what\s+is|calculate|calc|how\s+much\s+is)\s*",
        "",
        expr,
        flags=re.IGNORECASE,
    ).strip()
    expr = expr.rstrip("?").strip()
    expr = expr.replace("^", "**")

    if not expr:
        return "Please provide a valid math expression to calculate (e.g. 27 * 43)."

    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        return f"Invalid arithmetic expression: '{expression}'"

    def _eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_MATH_OPS:
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            return float(_SAFE_MATH_OPS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_MATH_OPS:
            operand = _eval_node(node.operand)
            return float(_SAFE_MATH_OPS[type(node.op)](operand))
        raise ValueError(f"Unsupported syntax or token: {type(node).__name__}")

    try:
        val = _eval_node(tree)
        # Format integer cleanly if whole number
        if val.is_integer():
            return str(int(val))
        return f"{val:.6g}"
    except ZeroDivisionError:
        return "Division by zero is undefined."
    except Exception as exc:
        return f"Could not evaluate expression: {exc}"


def get_system_ram_info() -> str:
    """Get clean system RAM statistics in <2ms."""
    try:
        # Check /proc/meminfo on Linux
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            data: dict[str, int] = {}
            with open(meminfo, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val_str = parts[1].strip().split()[0]
                        if val_str.isdigit():
                            data[key] = int(val_str)

            total_kb = data.get("MemTotal", 0)
            avail_kb = data.get("MemAvailable", 0)
            used_kb = max(0, total_kb - avail_kb)

            if total_kb > 0:
                total_gb = total_kb / (1024 * 1024)
                used_gb = used_kb / (1024 * 1024)
                avail_gb = avail_kb / (1024 * 1024)
                pct = (used_kb / total_kb) * 100.0
                return (
                    f"Memory: {used_gb:.1f} GB / {total_gb:.1f} GB used ({pct:.0f}%)  ·  "
                    f"{avail_gb:.1f} GB available"
                )
    except Exception as exc:
        logger.debug("Failed reading /proc/meminfo: %s", exc)

    return "System memory information could not be read."


def get_system_disk_info() -> str:
    """Get primary filesystem disk usage statistics in <1ms."""
    try:
        usage = shutil.disk_usage(Path.home())
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        free_gb = usage.free / (1024**3)
        pct = (usage.used / usage.total) * 100.0
        return f"Disk (Home): {used_gb:.1f} GB / {total_gb:.1f} GB used ({pct:.0f}%)  ·  {free_gb:.1f} GB free"
    except Exception as exc:
        return f"Could not determine disk usage: {exc}"


class CommandRegistry:
    """Central registry of executable commands, capability actions, and discovered applications."""

    _instance: CommandRegistry | None = None

    def __init__(
        self,
        capability_registry: CapabilityRegistry | None = None,
        app_registry: ApplicationRegistry | None = None,
    ) -> None:
        self.capabilities = capability_registry or create_default_capability_registry()
        self.apps = app_registry or ApplicationRegistry.get_instance()
        self._commands: dict[str, CommandDefinition] = {}
        self._aliases: dict[str, str] = {}
        self._register_builtin_commands()

    @classmethod
    def get_instance(cls) -> CommandRegistry:
        """Singleton accessor for CommandRegistry."""
        if cls._instance is None:
            cls._instance = CommandRegistry()
        return cls._instance

    def register_command(self, cmd: CommandDefinition) -> None:
        """Register a command definition under its ID and optional aliases."""
        clean_id = cmd.id.strip().lower()
        self._commands[clean_id] = cmd
        self._aliases[cmd.name.lower()] = clean_id
        for alias in cmd.aliases:
            self._aliases[alias.lower()] = clean_id

    def unregister_command(self, cmd_id: str) -> None:
        """Remove a registered command."""
        clean_id = cmd_id.strip().lower()
        if clean_id in self._commands:
            cmd = self._commands.pop(clean_id)
            self._aliases.pop(cmd.name.lower(), None)
            for alias in cmd.aliases:
                self._aliases.pop(alias.lower(), None)

    def get_command(self, identifier: str) -> CommandDefinition | None:
        """Find a command by ID, primary name, or alias."""
        clean = identifier.strip().lower()
        if clean.startswith("/"):
            clean = clean[1:].strip()

        if clean in self._commands:
            return self._commands[clean]
        if clean in self._aliases:
            return self._commands.get(self._aliases[clean])

        return None

    def _register_builtin_commands(self) -> None:
        """Register built-in system, calculation, diagnostic, and task commands."""
        # 1. Calculator
        self.register_command(
            CommandDefinition(
                id="calc",
                name="Calculator",
                description="Calculate arithmetic expressions instantly without AI",
                category=CommandCategory.COMMAND,
                icon="accessories-calculator",
                aliases=["calc", "calculate", "math"],
                keywords=["add", "subtract", "multiply", "divide", "eval", "math", "calculator"],
                handler=lambda arg: evaluate_safe_arithmetic(arg or ""),
                requires_argument=True,
                argument_hint="expression (e.g. 27 * 43)",
                actions=[
                    ActionResult(
                        id="calc:run",
                        name="Calculate expression",
                        description="Evaluate mathematical expression",
                        action_type="calculate",
                    )
                ],
            )
        )

        # 2. RAM / Memory
        self.register_command(
            CommandDefinition(
                id="ram",
                name="RAM usage",
                description="Show available system memory and RAM consumption",
                category=CommandCategory.SYSTEM,
                icon="utilities-system-monitor",
                aliases=["ram", "memory", "mem"],
                keywords=["ram", "memory", "usage", "available", "swap"],
                handler=lambda _arg: get_system_ram_info(),
                requires_argument=False,
                actions=[
                    ActionResult(
                        id="ram:view",
                        name="View memory stats",
                        description="Display current RAM usage",
                        action_type="execute",
                    )
                ],
            )
        )

        # 3. CPU
        self.register_command(
            CommandDefinition(
                id="cpu",
                name="CPU usage",
                description="Show CPU usage and busiest processes",
                category=CommandCategory.SYSTEM,
                icon="utilities-system-monitor",
                aliases=["cpu", "processor"],
                keywords=["cpu", "load", "processor", "performance"],
                handler=lambda _arg: self._get_cpu_info(),
                requires_argument=False,
                actions=[
                    ActionResult(
                        id="cpu:view",
                        name="View CPU usage",
                        description="Inspect CPU activity",
                        action_type="execute",
                    )
                ],
            )
        )

        # 4. Disk
        self.register_command(
            CommandDefinition(
                id="disk",
                name="Disk usage",
                description="Show storage and free disk space",
                category=CommandCategory.SYSTEM,
                icon="drive-harddisk",
                aliases=["disk", "space", "storage", "df"],
                keywords=["disk", "storage", "space", "free", "drive", "hdd", "ssd"],
                handler=lambda _arg: get_system_disk_info(),
                requires_argument=False,
                actions=[
                    ActionResult(
                        id="disk:view",
                        name="View disk space",
                        description="Check available disk storage",
                        action_type="execute",
                    )
                ],
            )
        )

        # 5. System Health / Doctor
        self.register_command(
            CommandDefinition(
                id="health",
                name="System health",
                description="Run AVI health diagnostics and self-checks",
                category=CommandCategory.SYSTEM,
                icon="preferences-system",
                aliases=["health", "doctor", "diag", "diagnose"],
                keywords=["doctor", "health", "check", "diagnose", "status", "system"],
                handler=lambda _arg: self._run_health_check(),
                requires_argument=False,
                actions=[
                    ActionResult(
                        id="health:run",
                        name="Run health check",
                        description="Perform diagnostic scan of subsystems",
                        action_type="execute",
                    )
                ],
            )
        )

        # 6. Filesystem search
        self.register_command(
            CommandDefinition(
                id="files",
                name="Search files",
                description="Find files and folders on your computer",
                category=CommandCategory.FILE,
                icon="system-file-manager",
                aliases=["files", "file", "find", "search_files"],
                keywords=["file", "search", "find", "document", "folder", "directory"],
                capability_name="filesystem.search",
                requires_argument=True,
                argument_hint="filename or pattern (e.g. *.pdf)",
                actions=[
                    ActionResult(
                        id="files:search",
                        name="Find files",
                        description="Search filesystem for query",
                        action_type="execute",
                    )
                ],
            )
        )

        # 7. PDF Search
        self.register_command(
            CommandDefinition(
                id="pdf",
                name="Search PDFs",
                description="Find PDF files across Downloads and Documents",
                category=CommandCategory.FILE,
                icon="application-pdf",
                aliases=["pdf", "pdfs", "find_pdf"],
                keywords=["pdf", "document", "file", "ebook"],
                handler=lambda arg: self._search_pdfs(arg or ""),
                requires_argument=False,
                argument_hint="optional filter query",
                actions=[
                    ActionResult(
                        id="pdf:search",
                        name="Search PDF files",
                        description="Locate PDF documents on computer",
                        action_type="execute",
                    )
                ],
            )
        )

        # 8. Persistent Task
        self.register_command(
            CommandDefinition(
                id="task",
                name="Create task",
                description="Create a persistent autonomous AVI agent task",
                category=CommandCategory.TASK,
                icon="system-run",
                aliases=["task", "goal", "plan"],
                keywords=["task", "agent", "autonomous", "plan", "persistent", "goal", "organize"],
                requires_argument=True,
                argument_hint="task goal (e.g. organize my downloads)",
                actions=[
                    ActionResult(
                        id="task:create",
                        name="Create agent task",
                        description="Launch task with agent planner",
                        action_type="task",
                    )
                ],
            )
        )

        # 9. Screenshot
        self.register_command(
            CommandDefinition(
                id="screenshot",
                name="Take screenshot",
                description="Capture full screen or active window natively",
                category=CommandCategory.COMMAND,
                icon="applets-screenshooter",
                aliases=["screenshot", "screen", "capture", "snip"],
                keywords=["screenshot", "screen", "capture", "display", "image"],
                capability_name="desktop.screenshot",
                requires_argument=False,
                actions=[
                    ActionResult(
                        id="screenshot:take",
                        name="Capture screen",
                        description="Save screenshot to Pictures",
                        action_type="execute",
                    )
                ],
            )
        )

        # 10. Volume
        self.register_command(
            CommandDefinition(
                id="volume",
                name="Audio volume",
                description="Check or adjust system output audio volume",
                category=CommandCategory.COMMAND,
                icon="audio-volume-high",
                aliases=["volume", "sound", "audio"],
                keywords=["volume", "sound", "audio", "mute", "speaker"],
                capability_name="desktop.volume.get",
                requires_argument=False,
                actions=[
                    ActionResult(
                        id="volume:get",
                        name="Get volume",
                        description="Check current audio volume",
                        action_type="execute",
                    )
                ],
            )
        )

        # 11. Command Palette Help
        self.register_command(
            CommandDefinition(
                id="help",
                name="Commands help",
                description="Show available command categories, applications, and shortcuts",
                category=CommandCategory.COMMAND,
                icon="help-browser",
                aliases=["help", "commands", "?"],
                keywords=["help", "commands", "categories", "options", "shortcuts"],
                handler=lambda _arg: self._format_help_summary(),
                requires_argument=False,
                actions=[
                    ActionResult(
                        id="help:show",
                        name="View commands",
                        description="List palette commands and shortcuts",
                        action_type="execute",
                    )
                ],
            )
        )

    def _get_cpu_info(self) -> str:
        """Inspect CPU usage quickly."""
        try:
            # Check loadavg on Linux
            load1, load5, load15 = os.getloadavg()
            num_cpus = os.cpu_count() or 1
            pct1 = (load1 / num_cpus) * 100.0
            return f"CPU Load: {pct1:.0f}% (1m avg: {load1:.2f}, 5m: {load5:.2f})  ·  {num_cpus} cores available"
        except Exception as exc:
            return f"CPU statistics could not be retrieved: {exc}"

    def _run_health_check(self) -> str:
        """Run health monitor and return clean CLI diagnostics."""
        from avi.reliability.health import HealthMonitor

        monitor = HealthMonitor()
        return monitor.run_doctor()

    def _search_pdfs(self, query: str = "") -> str:
        """Locate PDF files in Downloads and Documents."""
        cap = self.capabilities.get("filesystem.search")
        if cap:
            dirs = [str(Path.home() / "Downloads"), str(Path.home() / "Documents")]
            results: list[str] = []
            for d in dirs:
                if Path(d).is_dir():
                    res = cap.execute(directory=d, pattern=f"*{query}*.pdf" if query else "*.pdf")
                    if res.success and res.data and "files" in res.data:
                        results.extend(res.data["files"])
            if results:
                preview = "\n".join(f"  • {Path(p).name} ({p})" for p in results[:5])
                total = len(results)
                extra = f"\n  ...and {total - 5} more" if total > 5 else ""
                return f"Found {total} PDF files:\n{preview}{extra}"
            return "No PDF files found in Downloads or Documents."
        return "Filesystem search capability is unavailable."

    def _format_help_summary(self) -> str:
        """Format a clear overview of command palette capabilities."""
        return (
            "AVI Command Palette\n"
            "===================\n"
            "Type '/' to filter commands, or type an application name directly:\n\n"
            "  /calc <expr>     Calculate math expressions (e.g. /calc 27 * 43)\n"
            "  /chrome          Open or focus Google Chrome\n"
            "  /spotify         Open or control Spotify\n"
            "  /files [query]   Search files on computer\n"
            "  /pdf             Find PDF documents in Downloads & Documents\n"
            "  /ram             View available memory and RAM usage\n"
            "  /cpu             View CPU load and performance\n"
            "  /disk            View free storage space\n"
            "  /health          Run AVI health diagnostics\n"
            "  /task <goal>     Create a persistent autonomous agent task\n"
            "  /screenshot      Capture display screenshot\n"
            "  /volume          Inspect system volume\n\n"
            "Navigation: Up/Down to navigate  ·  Enter to run  ·  Tab for actions  ·  Esc to close"
        )

    def list_all_definitions(self) -> list[CommandDefinition]:
        """Return all static and dynamically registered commands."""
        return list(self._commands.values())

    def list_all_applications(self) -> list[ApplicationEntry]:
        """Return all discovered applications from the ApplicationRegistry."""
        return self.apps.list_all()

    def list_all_capabilities(self) -> list[BaseCapability]:
        """Return all registered agent capabilities."""
        return self.capabilities.get_all()
