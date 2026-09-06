"""Desktop application, URL, and file opening capabilities for AVI Agent Runtime."""

from pathlib import Path
from typing import Any, Sequence

from avi.actions.system import OpenDirAction, OpenFileAction, OpenUrlAction
from avi.apps.resolver import ApplicationResolver
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory


class LaunchAppCapability(BaseCapability):
    """Launch an installed desktop application by name."""

    name = "desktop.open_app"
    description = "Launch an installed desktop application by name (e.g. 'firefox', 'terminal', 'calculator')."
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
            data=act_res.data,
        )


class OpenFileCapability(BaseCapability):
    """Open a local file in its default system viewer."""

    name = "desktop.open_file"
    description = "Open a local file in its system default application."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the file"},
        },
        "required": ["path"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def execute(self, **kwargs: Any) -> CapabilityResult:
        path = kwargs.get("path") or kwargs.get("file") or kwargs.get("filename")
        if not path:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="File path parameter is required.",
            )
        action = OpenFileAction(Path(str(path)))
        act_res = action.execute()
        status = ExecutionStatus.SUCCESS if act_res.success else ExecutionStatus.FAILED
        return CapabilityResult(
            success=act_res.success,
            status=status,
            message=act_res.message,
            error=act_res.message if not act_res.success else None,
            data=act_res.data,
        )


class OpenDirectoryCapability(BaseCapability):
    """Open a local directory in the system file manager."""

    name = "desktop.open_directory"
    description = "Open a local directory in the default system file manager."
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
            data=act_res.data,
        )
