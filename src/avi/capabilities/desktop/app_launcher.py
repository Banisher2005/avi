import subprocess
from pathlib import Path
from typing import Any, Sequence

from avi.actions.system import OpenDirAction, OpenFileAction, OpenUrlAction
from avi.apps.resolver import ApplicationResolver
from avi.capabilities.models import (
    BaseCapability,
    CapabilityCategory,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory


class LaunchAppCapability(BaseCapability):
    """Launch an installed desktop application by name."""

    name = "desktop.open_app"
    description = "Launch an installed desktop application by name (e.g. 'firefox', 'terminal', 'calculator')."
    category = CapabilityCategory.APPLICATION
    side_effects = True
    supports_observation = True
    input_schema = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "The name or alias of the application to launch",
            },
            "extra_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional arguments to pass to the application",
            },
        },
        "required": ["app_name"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(self, resolver: ApplicationResolver | None = None) -> None:
        self.resolver = resolver or ApplicationResolver()

    def execute(self, **kwargs: Any) -> CapabilityResult:
        app_name = kwargs.get("app_name") or kwargs.get("name") or kwargs.get("app")
        if not app_name:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Application name must be specified.",
            )
        extra_args: Sequence[str] = kwargs.get("extra_args") or []
        res = self.resolver.resolve(str(app_name))
        if not res.found:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Could not find application '{app_name}'. Check if it is installed.",
                data={"resolution": res},
            )
        success, msg = self.resolver.launch(res, extra_args=extra_args)
        status = ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILED
        return CapabilityResult(
            success=success,
            status=status,
            message=msg,
            error=None if success else msg,
            data={"resolution": res, "app_name": app_name},
        )


class OpenUrlCapability(BaseCapability):
    """Open a web URL in the default web browser."""

    name = "desktop.open_url"
    description = "Open an HTTP or HTTPS web URL in the system default web browser."
    category = CapabilityCategory.APPLICATION
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The web URL to open (e.g. 'https://github.com')",
            },
        },
        "required": ["url"],
    }
    risk_category = ActionCategory.EXTERNAL_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True

    def execute(self, **kwargs: Any) -> CapabilityResult:
        url = kwargs.get("url") or kwargs.get("address")
        if not url:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="URL parameter is required.",
            )
        action = OpenUrlAction(str(url))
        act_res = action.execute()
        status = ExecutionStatus.SUCCESS if act_res.success else ExecutionStatus.FAILED
        return CapabilityResult(
            success=act_res.success,
            status=status,
            message=act_res.message,
            error=act_res.message if not act_res.success else None,
            data=act_res.data or {"url": str(url)},
        )


class OpenFileCapability(BaseCapability):
    """Open a local file in its default system viewer or a specified application."""

    name = "desktop.open_file"
    description = "Open a local file in its system default application or a specified app."
    category = CapabilityCategory.APPLICATION
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the file"},
            "app_name": {"type": "string", "description": "Optional specific application to open the file with (e.g. 'code', 'gedit', 'vlc')"},
        },
        "required": ["path"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True

    def __init__(self, resolver: ApplicationResolver | None = None) -> None:
        self.resolver = resolver or ApplicationResolver()

    def execute(self, **kwargs: Any) -> CapabilityResult:
        path = kwargs.get("path") or kwargs.get("file") or kwargs.get("filename")
        if not path:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="File path parameter is required.",
            )
        target_path = Path(str(path)).expanduser().resolve()
        app_name = kwargs.get("app_name") or kwargs.get("app")

        if app_name:
            res = self.resolver.resolve(str(app_name))
            if res.found and res.executable:
                try:
                    subprocess.Popen(
                        [res.executable, str(target_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Opened {target_path.name} in {res.canonical_name}.",
                        data={"path": str(target_path), "app": res.canonical_name},
                        classification=self.data_classification,
                    )
                except Exception as err:
                    return CapabilityResult(
                        success=False,
                        status=ExecutionStatus.FAILED,
                        error=str(err),
                        message=f"Failed to open with {app_name}: {err}",
                    )

        action = OpenFileAction(target_path)
        act_res = action.execute()
        status = ExecutionStatus.SUCCESS if act_res.success else ExecutionStatus.FAILED
        return CapabilityResult(
            success=act_res.success,
            status=status,
            message=act_res.message,
            error=act_res.message if not act_res.success else None,
            data=act_res.data or {"path": str(target_path)},
        )


class OpenDirectoryCapability(BaseCapability):
    """Open a local directory in the system file manager."""

    name = "desktop.open_directory"
    description = "Open a local directory in the default system file manager."
    category = CapabilityCategory.APPLICATION
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the directory"},
        },
        "required": ["path"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True

    def execute(self, **kwargs: Any) -> CapabilityResult:
        path = kwargs.get("path") or kwargs.get("directory") or kwargs.get("dir")
        if not path:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Directory path parameter is required.",
            )
        action = OpenDirAction(Path(str(path)))
        act_res = action.execute()
        status = ExecutionStatus.SUCCESS if act_res.success else ExecutionStatus.FAILED
        return CapabilityResult(
            success=act_res.success,
            status=status,
            message=act_res.message,
            error=act_res.message if not act_res.success else None,
            data=act_res.data or {"path": str(path)},
        )


class CloseAppCapability(BaseCapability):
    """Gracefully terminate or close a running application by name."""

    name = "desktop.close_app"
    description = "Gracefully close or terminate a running desktop application by name."
    category = CapabilityCategory.APPLICATION
    input_schema = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of the application to close (e.g. 'chrome', 'firefox', 'vlc')",
            },
        },
        "required": ["app_name"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True
    tags = ("desktop", "app", "application", "close", "terminate", "kill")

    def __init__(self, resolver: ApplicationResolver | None = None) -> None:
        self.resolver = resolver or ApplicationResolver()

    def execute(self, **kwargs: Any) -> CapabilityResult:
        app_name = kwargs.get("app_name") or kwargs.get("name") or kwargs.get("app")
        if not app_name:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Application name parameter is required.",
                message="Cannot close: no application name specified.",
            )
        clean_name = str(app_name).strip()
        res = self.resolver.resolve(clean_name)
        target_process = res.canonical_name if res.found else clean_name

        try:
            # 1. Try pkill with case-insensitive process match
            proc = subprocess.run(
                ["pkill", "-f", "-i", target_process],
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
                    data={"app_name": clean_name, "process": target_process},
                    message=f"Closed {clean_name}.",
                    classification=self.data_classification,
                )

            # 2. Try executable basename if different
            if res.executable and Path(res.executable).name != target_process:
                proc2 = subprocess.run(
                    ["pkill", "-f", "-i", Path(res.executable).name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=3.0,
                )
                if proc2.returncode == 0:
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        data={"app_name": clean_name, "process": Path(res.executable).name},
                        message=f"Closed {clean_name}.",
                        classification=self.data_classification,
                    )

            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"app_name": clean_name, "running": False},
                message=f"Application '{clean_name}' does not appear to be running.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to close application '{clean_name}': {err}",
            )
