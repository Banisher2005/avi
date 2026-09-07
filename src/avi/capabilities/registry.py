"""Central registry for discovering, validating, and executing capabilities."""

import inspect
from typing import Any, Iterator, Sequence

from avi.apps.resolver import ApplicationResolver
from avi.capabilities.desktop.app_launcher import (
    LaunchAppCapability,
    OpenDirectoryCapability,
    OpenFileCapability,
    OpenUrlCapability,
)
from avi.capabilities.desktop.notification import NotificationCapability
from avi.capabilities.desktop.screenshot import ScreenshotCapability
from avi.capabilities.desktop.system_controls import (
    MediaControlCapability,
    VolumeGetCapability,
    VolumeSetCapability,
)
from avi.capabilities.filesystem.operations import (
    CopyFileCapability,
    CreateDirectoryCapability,
    DeleteFileCapability,
    MoveFileCapability,
)
from avi.capabilities.filesystem.search import FilesystemSearchCapability
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    ExecutionStatus,
    ToolCapabilityAdapter,
)
from avi.capabilities.web.search import (
    YouTubeSearchCapability,
    YouTubeSearchResultsCapability,
)
from avi.safety.models import ActionCategory
from avi.tools.registry import ToolRegistry, create_default_registry


class CapabilityRegistry:
    """Registry maintaining available agent capabilities."""

    def __init__(self) -> None:
        self._capabilities: dict[str, BaseCapability] = {}
        self._aliases: dict[str, str] = {}

    def register(self, capability: BaseCapability, aliases: Sequence[str] | None = None) -> None:
        """Register a capability under its primary name and optional aliases."""
        self._capabilities[capability.name] = capability
        combined_aliases = list(aliases or [])
        if hasattr(capability, "aliases"):
            combined_aliases.extend(capability.aliases)
        for alias in combined_aliases:
            self._aliases[alias] = capability.name

    def get(self, name: str) -> BaseCapability | None:
        """Look up a capability by primary name or alias."""
        if name in self._capabilities:
            return self._capabilities[name]
        canonical = self._aliases.get(name)
        if canonical and canonical in self._capabilities:
            return self._capabilities[canonical]
        return None

    def list_capabilities(self) -> list[str]:
        """Return names of all registered primary capabilities."""
        return sorted(self._capabilities.keys())

    def get_all(self) -> list[BaseCapability]:
        """Return all unique registered capabilities."""
        return list(self._capabilities.values())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Return metadata schemas for all registered capabilities."""
        return [c.to_metadata() for c in self._capabilities.values()]

    def execute(self, name: str, **kwargs: Any) -> CapabilityResult:
        """Execute capability by name with arguments."""
        cap = self.get(name)
        if cap is None:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Capability '{name}' not found.",
                message=f"Unknown capability: {name}",
            )
        try:
            return cap.execute(**kwargs)
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Capability '{name}' execution failed: {err}",
            )

    def execute_safe(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        safety_engine: Any = None,
        confirmed: bool = False,
    ) -> CapabilityResult:
        """Execute capability through safety evaluation and confirmation checks."""
        cap = self.get(name)
        if cap is None:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Capability '{name}' not found.",
                message=f"Unknown capability: {name}",
            )

        kwargs = dict(args or {})

        # If confirmation is required and not confirmed, fail-safe
        if not confirmed and (
            cap.requires_confirmation
            or cap.risk_category in (ActionCategory.DESTRUCTIVE, ActionCategory.PRIVILEGED)
        ):
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.CONFIRMATION_REQUIRED,
                message=f"Action '{name}' requires explicit user confirmation before proceeding.",
                data={"action": name, "args": kwargs, "requires_confirmation": True},
            )

        try:
            # Check if capability accepts 'confirmed' kwarg
            sig = inspect.signature(cap.execute)
            if "confirmed" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                kwargs["confirmed"] = confirmed
            return cap.execute(**kwargs)
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Capability '{name}' execution failed: {err}",
            )

    def __contains__(self, name: str) -> bool:
        return name in self._capabilities or name in self._aliases

    def __len__(self) -> int:
        return len(self._capabilities)

    def __iter__(self) -> Iterator[BaseCapability]:
        return iter(self._capabilities.values())


def create_default_capability_registry(
    tools: ToolRegistry | None = None,
    resolver: ApplicationResolver | None = None,
) -> CapabilityRegistry:
    """Instantiate and populate the default capability registry."""
    registry = CapabilityRegistry()

    # 1. Read-only Tools
    tool_reg = tools or create_default_registry()
    for tool in tool_reg.list_tools():
        adapter = ToolCapabilityAdapter(tool)
        # Register under original name (e.g. system.disk_usage) and aliases
        aliases = []
        if tool.name == "system.disk_usage":
            aliases.append("system.disk")
        registry.register(adapter, aliases=aliases)

    # 2. Desktop Capabilities
    app_resolver = resolver or ApplicationResolver()
    registry.register(ScreenshotCapability(), aliases=["screenshot", "take_screenshot"])
    registry.register(NotificationCapability(), aliases=["notification", "notify"])
    registry.register(
        VolumeGetCapability(),
        aliases=["volume.get", "desktop.volume.get", "get_volume"],
    )
    registry.register(
        VolumeSetCapability(),
        aliases=["volume.set", "desktop.volume.set", "set_volume"],
    )
    registry.register(
        MediaControlCapability(),
        aliases=["desktop.media.control", "desktop.media", "media", "media_control"],
    )
    registry.register(LaunchAppCapability(app_resolver), aliases=["app.launch", "open_app"])
    registry.register(
        OpenUrlCapability(),
        aliases=["open_url", "desktop.open_url", "desktop.url.open"],
    )
    registry.register(OpenFileCapability(), aliases=["open_file"])
    registry.register(OpenDirectoryCapability(), aliases=["open_directory", "open_folder"])

    # 3. Web Capabilities
    url_opener = registry.get("desktop.open_url")
    registry.register(
        YouTubeSearchCapability(url_capability=url_opener),
        aliases=["youtube.search", "youtube_search"],
    )
    registry.register(
        YouTubeSearchResultsCapability(),
        aliases=["youtube.search_results", "youtube_search_results", "youtube.retrieve"],
    )

    # 4. Filesystem Capabilities
    registry.register(FilesystemSearchCapability(), aliases=["file_search", "find_file"])
    registry.register(CreateDirectoryCapability(), aliases=["mkdir", "create_dir"])
    registry.register(CopyFileCapability(), aliases=["copy_file", "cp"])
    registry.register(MoveFileCapability(), aliases=["move_file", "mv"])
    registry.register(DeleteFileCapability(), aliases=["delete_file", "rm"])

    return registry
