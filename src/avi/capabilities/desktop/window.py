"""Window management capabilities for AVI Agent Runtime."""

import logging
import shutil
import subprocess
from typing import Any

from avi.apps.resolver import ApplicationResolver
from avi.capabilities.models import (
    BaseCapability,
    CapabilityCategory,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger("avi.capabilities.desktop.window")


def parse_wmctrl_lxp(output: str) -> list[dict[str, Any]]:
    """Parse output of `wmctrl -lxp` into structured window records."""
    windows: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # Expected format: <id> <desktop> <pid> <class> <client> <title...>
        parts = line.split(maxsplit=5)
        if len(parts) >= 6:
            win_id, desktop_str, pid_str, wm_class, client, title = parts
            try:
                pid = int(pid_str) if pid_str != "0" else None
            except ValueError:
                pid = None
            try:
                desktop = int(desktop_str)
            except ValueError:
                desktop = 0
            windows.append(
                {
                    "id": win_id,
                    "desktop": desktop,
                    "pid": pid,
                    "wm_class": wm_class,
                    "client_machine": client,
                    "title": title.strip(),
                }
            )
        elif len(parts) >= 4:
            # Fallback format: <id> <desktop> <client> <title...>
            win_id = parts[0]
            try:
                desktop = int(parts[1])
            except ValueError:
                desktop = 0
            client = parts[2]
            title = " ".join(parts[3:]).strip()
            windows.append(
                {
                    "id": win_id,
                    "desktop": desktop,
                    "pid": None,
                    "wm_class": "",
                    "client_machine": client,
                    "title": title,
                }
            )
    return windows


def list_system_windows(timeout: float = 3.0) -> list[dict[str, Any]]:
    """Query current desktop windows using available system utilities."""
    # 1. Try wmctrl -lxp
    if shutil.which("wmctrl"):
        try:
            proc = subprocess.run(
                ["wmctrl", "-lxp"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return parse_wmctrl_lxp(proc.stdout)
            # Try wmctrl -l as fallback
            proc_l = subprocess.run(
                ["wmctrl", "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc_l.returncode == 0:
                return parse_wmctrl_lxp(proc_l.stdout)
        except Exception as err:
            logger.debug("wmctrl execution error: %s", err)

    # 2. Try xdotool search if wmctrl is not present
    if shutil.which("xdotool"):
        try:
            proc = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", ""],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                win_ids = [w.strip() for w in proc.stdout.splitlines() if w.strip()]
                windows = []
                for wid in win_ids[:20]:  # limit probe count
                    name_proc = subprocess.run(
                        ["xdotool", "getwindowname", wid],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                        timeout=1.0,
                    )
                    title = name_proc.stdout.strip() if name_proc.returncode == 0 else ""
                    if title:
                        windows.append(
                            {
                                "id": hex(int(wid)) if wid.isdigit() else wid,
                                "desktop": 0,
                                "pid": None,
                                "wm_class": "",
                                "client_machine": "localhost",
                                "title": title,
                            }
                        )
                if windows:
                    return windows
        except Exception as err:
            logger.debug("xdotool execution error: %s", err)

    return []


def focus_system_window(
    target: str,
    window_id: str | None = None,
    timeout: float = 3.0,
) -> tuple[bool, str]:
    """Bring a window into focus by window ID, title substring, or class name."""
    # 1. By window ID via wmctrl -i -a
    if window_id and shutil.which("wmctrl"):
        try:
            proc = subprocess.run(
                ["wmctrl", "-i", "-a", window_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, f"Focused window with ID '{window_id}'."
        except Exception as err:
            logger.debug("wmctrl focus by ID failed: %s", err)

    # 2. By target string via wmctrl -a
    if target and shutil.which("wmctrl"):
        try:
            proc = subprocess.run(
                ["wmctrl", "-a", target],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, f"Focused window matching '{target}'."
        except Exception as err:
            logger.debug("wmctrl focus by target failed: %s", err)

    # 3. Try xdotool windowactivate
    if target and shutil.which("xdotool"):
        try:
            search_proc = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", target],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if search_proc.returncode == 0 and search_proc.stdout.strip():
                wid = search_proc.stdout.splitlines()[0].strip()
                act_proc = subprocess.run(
                    ["xdotool", "windowactivate", wid],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=timeout,
                )
                if act_proc.returncode == 0:
                    return True, f"Focused window '{wid}' matching '{target}' via xdotool."
        except Exception as err:
            logger.debug("xdotool focus failed: %s", err)

    return False, f"Could not find or focus window matching '{target or window_id}'."


class WindowListCapability(BaseCapability):
    """List currently open desktop windows with metadata."""

    name = "desktop.window.list"
    description = "List currently open desktop windows including title, class, ID, and desktop number."
    category = CapabilityCategory.APPLICATION
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional search term to filter windows by title or application class",
            },
        },
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = False
    supports_observation = True
    tags = ("desktop", "window", "list", "windows", "inspect")

    def execute(self, **kwargs: Any) -> CapabilityResult:
        query = (kwargs.get("query") or kwargs.get("filter") or "").strip().lower()

        try:
            windows = list_system_windows()
            if query:
                windows = [
                    w
                    for w in windows
                    if query in w.get("title", "").lower()
                    or query in w.get("wm_class", "").lower()
                ]

            count = len(windows)
            if count == 0:
                msg = f"No open windows found matching '{query}'." if query else "No open desktop windows detected."
            else:
                titles = [w.get("title") or w.get("wm_class") or w["id"] for w in windows[:5]]
                summary = ", ".join(repr(t) for t in titles)
                if count > 5:
                    summary += f" and {count - 5} more"
                msg = f"Found {count} open window(s): {summary}"

            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=msg,
                data={"windows": windows, "count": count},
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to list windows: {err}",
            )


class WindowFocusCapability(BaseCapability):
    """Focus / bring to front an open desktop window."""

    name = "desktop.window.focus"
    description = "Bring an open desktop window to the front and focus it by title, class, or window ID."
    category = CapabilityCategory.APPLICATION
    input_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Window title or application name to focus",
            },
            "window_id": {
                "type": "string",
                "description": "Optional specific window ID (e.g. '0x02800003')",
            },
        },
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True
    tags = ("desktop", "window", "focus", "activate", "switch")

    def __init__(self, resolver: ApplicationResolver | None = None) -> None:
        super().__init__()
        self._resolver = resolver

    def execute(self, **kwargs: Any) -> CapabilityResult:
        target = kwargs.get("title") or kwargs.get("app_name") or kwargs.get("target") or kwargs.get("query")
        window_id = kwargs.get("window_id") or kwargs.get("id")

        if not target and not window_id:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Either 'title', 'app_name', or 'window_id' must be specified to focus a window.",
            )

        target_str = str(target).strip() if target else ""
        wid_str = str(window_id).strip() if window_id else None

        # 1. Try system window focus utilities
        success, message = focus_system_window(target=target_str, window_id=wid_str)
        if success:
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=message,
                data={"target": target_str, "window_id": wid_str},
                classification=self.data_classification,
            )

        # 2. Fallback: If target matches an application and resolver is provided, activate it
        if target_str and self._resolver:
            resolution = self._resolver.resolve(target_str)
            if resolution.found and resolution.app:
                launch_ok, launch_msg = self._resolver.launch(resolution.app)
                if launch_ok:
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Activated application '{resolution.app.name}' for '{target_str}'.",
                        data={"target": target_str, "app": resolution.app.name},
                        classification=self.data_classification,
                    )

        return CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error=message,
            message=message,
        )


class WindowCloseCapability(BaseCapability):
    """Close an open desktop window by title, application class, or window ID."""

    name = "desktop.window.close"
    description = "Close an open desktop window by title, application class, or window ID."
    category = CapabilityCategory.APPLICATION
    input_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Window title or application name to close",
            },
            "window_id": {
                "type": "string",
                "description": "Optional specific window ID (e.g. '0x02800003')",
            },
        },
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True
    tags = ("desktop", "window", "close", "kill")

    def execute(self, **kwargs: Any) -> CapabilityResult:
        target = kwargs.get("title") or kwargs.get("app_name") or kwargs.get("target") or kwargs.get("query")
        window_id = kwargs.get("window_id") or kwargs.get("id")

        if not target and not window_id:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Either 'title', 'app_name', or 'window_id' must be specified to close a window.",
            )

        target_str = str(target).strip() if target else ""
        wid_str = str(window_id).strip() if window_id else None

        # 1. By window ID via wmctrl -i -c
        if wid_str and shutil.which("wmctrl"):
            try:
                proc = subprocess.run(
                    ["wmctrl", "-i", "-c", wid_str],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=3.0,
                )
                if proc.returncode == 0:
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Closed window with ID '{wid_str}'.",
                        data={"window_id": wid_str},
                        classification=self.data_classification,
                    )
            except Exception as err:
                logger.debug("wmctrl close by ID failed: %s", err)

        # 2. By title via wmctrl -c
        if target_str and shutil.which("wmctrl"):
            try:
                proc = subprocess.run(
                    ["wmctrl", "-c", target_str],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=3.0,
                )
                if proc.returncode == 0:
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Closed window matching '{target_str}'.",
                        data={"target": target_str},
                        classification=self.data_classification,
                    )
            except Exception as err:
                logger.debug("wmctrl close by title failed: %s", err)

        # 3. Try xdotool
        if target_str and shutil.which("xdotool"):
            try:
                search_proc = subprocess.run(
                    ["xdotool", "search", "--onlyvisible", "--name", target_str],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=3.0,
                )
                if search_proc.returncode == 0 and search_proc.stdout.strip():
                    wid = search_proc.stdout.splitlines()[0].strip()
                    close_proc = subprocess.run(
                        ["xdotool", "windowclose", wid],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                        timeout=3.0,
                    )
                    if close_proc.returncode == 0:
                        return CapabilityResult(
                            success=True,
                            status=ExecutionStatus.SUCCESS,
                            message=f"Closed window '{wid}' matching '{target_str}' via xdotool.",
                            data={"target": target_str, "wid": wid},
                            classification=self.data_classification,
                        )
            except Exception as err:
                logger.debug("xdotool close failed: %s", err)

        return CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error=f"Could not find or close window matching '{target_str or wid_str}'.",
            message=f"Could not close window matching '{target_str or wid_str}'.",
        )
