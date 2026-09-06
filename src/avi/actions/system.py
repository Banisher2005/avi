"""Desktop and system actions for applications, URLs, files, and directories."""

import os
from pathlib import Path
import shutil
import subprocess
import urllib.parse
import webbrowser
from typing import Sequence

from avi.actions.base import ActionResult, BaseAction
from avi.apps.models import ApplicationResolution
from avi.apps.resolver import ApplicationResolver
from avi.safety.models import ActionCategory


class OpenAppAction(BaseAction):
    """Launch a resolved desktop application."""

    name = "assistant.open_app"
    category = ActionCategory.LOW_RISK_ACTION
    requires_confirmation = False

    def __init__(
        self,
        resolution: ApplicationResolution,
        resolver: ApplicationResolver | None = None,
        extra_args: Sequence[str] | None = None,
    ) -> None:
        self.resolution = resolution
        self.resolver = resolver or ApplicationResolver()
        self.extra_args = list(extra_args) if extra_args is not None else []

    def execute(self) -> ActionResult:
        """Launch the application executable via resolver."""
        success, message = self.resolver.launch(self.resolution, extra_args=self.extra_args)
        return ActionResult(
            success=success,
            message=message,
            data={"resolution": self.resolution},
        )


class OpenUrlAction(BaseAction):
    """Open an HTTP or HTTPS web address in the user's default browser."""

    name = "assistant.open_url"
    category = ActionCategory.EXTERNAL_ACTION
    requires_confirmation = False

    def __init__(self, url: str) -> None:
        self.raw_url = url.strip()
        self.url = self._normalize_url(self.raw_url)

    def _normalize_url(self, raw: str) -> str:
        """Ensure URL has a valid web protocol scheme."""
        if raw.startswith(("http://", "https://")):
            return raw
        # If it looks like a domain (e.g., youtube.com, github.com), prepend https://
        if "." in raw and not raw.startswith("/"):
            return f"https://{raw}"
        return raw

    def execute(self) -> ActionResult:
        """Open URL in browser using standard library webbrowser or xdg-open."""
        if not self.url.startswith(("http://", "https://")):
            return ActionResult(
                success=False,
                message=f"Invalid web URL: '{self.raw_url}'. Must use http:// or https://.",
            )

        try:
            # Try webbrowser first
            opened = webbrowser.open(self.url)
            if not opened:
                # Fallback to xdg-open on Linux
                subprocess.Popen(
                    ["xdg-open", self.url],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return ActionResult(
                success=True,
                message=f"Opening {self.url}.",
                data={"url": self.url},
            )
        except Exception as err:
            return ActionResult(
                success=False,
                message=f"Failed to open URL '{self.url}': {err}",
            )


class OpenFileAction(BaseAction):
    """Open a local file in its system default associated viewer."""

    name = "assistant.open_file"
    category = ActionCategory.LOW_RISK_ACTION
    requires_confirmation = False

    def __init__(self, path: str | Path) -> None:
        self.target_path = Path(path).expanduser().resolve()

    def execute(self) -> ActionResult:
        """Open the file using xdg-open."""
        if not self.target_path.exists():
            return ActionResult(
                success=False,
                message=f"File does not exist: {self.target_path}",
            )
        if self.target_path.is_dir():
            return ActionResult(
                success=False,
                message=f"Path is a directory, not a file: {self.target_path}",
            )

        try:
            subprocess.Popen(
                ["xdg-open", str(self.target_path)],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return ActionResult(
                success=True,
                message=f"Opening file {self.target_path.name}.",
                data={"path": str(self.target_path)},
            )
        except Exception as err:
            return ActionResult(
                success=False,
                message=f"Failed to open file: {err}",
            )


class OpenDirAction(BaseAction):
    """Open a directory in the default file manager."""

    name = "assistant.open_directory"
    category = ActionCategory.LOW_RISK_ACTION
    requires_confirmation = False

    def __init__(self, path: str | Path) -> None:
        self.target_path = Path(path).expanduser().resolve()

    def execute(self) -> ActionResult:
        """Open directory in file manager using xdg-open."""
        if not self.target_path.exists():
            return ActionResult(
                success=False,
                message=f"Directory does not exist: {self.target_path}",
            )
        if not self.target_path.is_dir():
            return ActionResult(
                success=False,
                message=f"Path is a file, not a directory: {self.target_path}",
            )

        try:
            subprocess.Popen(
                ["xdg-open", str(self.target_path)],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return ActionResult(
                success=True,
                message=f"Opening folder {self.target_path.name or str(self.target_path)}.",
                data={"path": str(self.target_path)},
            )
        except Exception as err:
            return ActionResult(
                success=False,
                message=f"Failed to open directory: {err}",
            )
