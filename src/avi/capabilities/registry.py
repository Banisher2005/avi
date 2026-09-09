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
from avi.capabilities.desktop.clipboard import (
    ClipboardGetCapability,
    ClipboardSetCapability,
)
from avi.capabilities.desktop.input import (
    PressKeyCapability,
    TypeTextCapability,
)
from avi.capabilities.desktop.notification import NotificationCapability
from avi.capabilities.desktop.screenshot import ScreenshotCapability
from avi.capabilities.desktop.system_controls import (
    MediaControlCapability,
    VolumeGetCapability,
    VolumeSetCapability,
)
from avi.capabilities.desktop.window import (
    WindowFocusCapability,
    WindowListCapability,
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
from avi.capabilities.browser import (
    BrowserClickCapability,
    BrowserExtractCapability,
    BrowserNavigateCapability,
    BrowserObserveCapability,
    BrowserTypeCapability,
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

    def get_capability(self, name: str) -> BaseCapability | None:
        """Alias for get() to retrieve capability by name or alias."""
        return self.get(name)

    def list_capabilities(self) -> list[str]:
        """Return names of all registered primary capabilities."""
        return sorted(self._capabilities.keys())

    def get_all(self) -> list[BaseCapability]:
        """Return all unique registered capabilities."""
        return list(self._capabilities.values())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Return metadata schemas for all registered capabilities."""
        return [c.to_metadata() for c in self._capabilities.values()]

    def get_model_catalog(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """Return a compact, model-facing catalog of registered capabilities."""
        catalog = []
        for cap in self._capabilities.values():
            if enabled_only and not getattr(cap, "enabled", True):
                continue
            catalog.append(cap.to_metadata())
        return catalog

    def search_capabilities(self, query: str) -> list[BaseCapability]:
        """Search capabilities by query across name, aliases, description, and tags."""
        q = query.strip().lower()
        if not q:
            return self.get_all()

        results: list[BaseCapability] = []
        seen: set[str] = set()

        for cap in self._capabilities.values():
            # Check primary name
            score = 0
            if q == cap.name.lower():
                score += 10
            elif q in cap.name.lower():
                score += 5

            # Check description
            if q in cap.description.lower():
                score += 3

            # Check tags
            for tag in getattr(cap, "tags", ()):
                if q == tag.lower():
                    score += 4
                elif q in tag.lower():
                    score += 2

            # Check aliases
            for alias, target in self._aliases.items():
                if target == cap.name:
                    if q == alias.lower():
                        score += 5
                    elif q in alias.lower():
                        score += 2

            if score > 0 and cap.name not in seen:
                seen.add(cap.name)
                results.append(cap)

        return results

    def set_capability_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a capability by name or alias."""
        cap = self.get(name)
        if cap is not None:
            cap.enabled = enabled
            return True
        return False

    def capability_count(self, enabled_only: bool = False) -> int:
        """Return count of registered capabilities."""
        if enabled_only:
            return sum(1 for c in self._capabilities.values() if getattr(c, "enabled", True))
        return len(self._capabilities)

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
        adapter.tags = ("tool", "system", "read_only")
        # Register under original name (e.g. system.disk_usage) and aliases
        aliases = []
        if tool.name == "system.disk_usage":
            aliases.append("system.disk")
        registry.register(adapter, aliases=aliases)

    # 2. Desktop Capabilities
    app_resolver = resolver or ApplicationResolver()
    
    shot_cap = ScreenshotCapability()
    shot_cap.tags = ("desktop", "screen", "capture", "image")
    registry.register(shot_cap, aliases=["screenshot", "take_screenshot"])

    notify_cap = NotificationCapability()
    notify_cap.tags = ("desktop", "notification", "alert")
    registry.register(notify_cap, aliases=["notification", "notify"])

    vol_get = VolumeGetCapability()
    vol_get.tags = ("desktop", "audio", "volume", "sound")
    registry.register(
        vol_get,
        aliases=["volume.get", "desktop.volume.get", "get_volume"],
    )

    vol_set = VolumeSetCapability()
    vol_set.tags = ("desktop", "audio", "volume", "sound")
    registry.register(
        vol_set,
        aliases=["volume.set", "desktop.volume.set", "set_volume"],
    )

    media_cap = MediaControlCapability()
    media_cap.tags = ("desktop", "media", "playback", "audio", "video")
    registry.register(
        media_cap,
        aliases=["desktop.media.control", "desktop.media", "media", "media_control"],
    )

    clip_get = ClipboardGetCapability()
    clip_get.tags = ("desktop", "clipboard", "read", "get", "paste")
    registry.register(
        clip_get,
        aliases=["clipboard.get", "desktop.clipboard.get", "clipboard.read", "get_clipboard"],
    )

    clip_set = ClipboardSetCapability()
    clip_set.tags = ("desktop", "clipboard", "write", "set", "copy")
    registry.register(
        clip_set,
        aliases=["clipboard.set", "desktop.clipboard.set", "clipboard.write", "set_clipboard", "copy_to_clipboard"],
    )

    launch_cap = LaunchAppCapability(app_resolver)
    launch_cap.tags = ("desktop", "app", "application", "launch")
    registry.register(launch_cap, aliases=["app.launch", "open_app"])

    url_cap = OpenUrlCapability()
    url_cap.tags = ("desktop", "browser", "web", "url")
    registry.register(
        url_cap,
        aliases=["open_url", "desktop.open_url", "desktop.url.open"],
    )

    file_cap = OpenFileCapability()
    file_cap.tags = ("desktop", "filesystem", "file", "open")
    registry.register(file_cap, aliases=["open_file"])

    dir_cap = OpenDirectoryCapability()
    dir_cap.tags = ("desktop", "filesystem", "directory", "folder")
    registry.register(dir_cap, aliases=["open_directory", "open_folder"])

    win_list = WindowListCapability()
    win_list.tags = ("desktop", "window", "list", "windows", "inspect")
    registry.register(
        win_list,
        aliases=["window.list", "desktop.list_windows", "list_windows", "get_windows"],
    )

    win_focus = WindowFocusCapability(resolver=app_resolver)
    win_focus.tags = ("desktop", "window", "focus", "activate", "switch")
    registry.register(
        win_focus,
        aliases=["window.focus", "desktop.focus_window", "focus_window", "activate_window", "switch_to_window"],
    )

    type_cap = TypeTextCapability()
    type_cap.tags = ("desktop", "input", "keyboard", "type", "text")
    registry.register(
        type_cap,
        aliases=["type_text", "desktop.type_text", "input.type", "type_string"],
    )

    press_cap = PressKeyCapability()
    press_cap.tags = ("desktop", "input", "keyboard", "key", "press", "hotkey")
    registry.register(
        press_cap,
        aliases=["press_key", "desktop.press_key", "input.press", "send_key", "hotkey"],
    )

    # 3. Web Capabilities
    url_opener = registry.get("desktop.open_url")
    yt_search = YouTubeSearchCapability(url_capability=url_opener)
    yt_search.tags = ("web", "youtube", "search", "video")
    registry.register(
        yt_search,
        aliases=["youtube.search", "youtube_search"],
    )

    yt_results = YouTubeSearchResultsCapability()
    yt_results.tags = ("web", "youtube", "search", "video")
    registry.register(
        yt_results,
        aliases=["youtube.search_results", "youtube_search_results", "youtube.retrieve"],
    )

    browser_observe = BrowserObserveCapability()
    registry.register(
        browser_observe,
        aliases=["browser.observe", "browser.state", "observe_browser", "get_browser_state"],
    )

    browser_nav = BrowserNavigateCapability()
    registry.register(
        browser_nav,
        aliases=["browser.navigate", "navigate", "browser.go", "goto_url", "browser.open"],
    )

    browser_extract = BrowserExtractCapability()
    registry.register(
        browser_extract,
        aliases=[
            "browser.extract",
            "browser.read",
            "read_page",
            "extract_page",
            "browser.read_page",
            "get_page_text",
        ],
    )

    browser_type = BrowserTypeCapability()
    registry.register(
        browser_type,
        aliases=["browser.type", "browser.input", "type_into_browser", "web.type"],
    )

    browser_click = BrowserClickCapability()
    registry.register(
        browser_click,
        aliases=["browser.click", "click_element", "browser.click_element", "web.click"],
    )

    # 4. Filesystem Capabilities
    fs_search = FilesystemSearchCapability()
    fs_search.tags = ("filesystem", "search", "find", "file")
    registry.register(fs_search, aliases=["file_search", "find_file"])

    fs_mkdir = CreateDirectoryCapability()
    fs_mkdir.tags = ("filesystem", "directory", "mkdir", "create")
    registry.register(fs_mkdir, aliases=["mkdir", "create_dir"])

    fs_cp = CopyFileCapability()
    fs_cp.tags = ("filesystem", "file", "copy")
    registry.register(fs_cp, aliases=["copy_file", "cp"])

    fs_mv = MoveFileCapability()
    fs_mv.tags = ("filesystem", "file", "move")
    registry.register(fs_mv, aliases=["move_file", "mv"])

    fs_rm = DeleteFileCapability()
    fs_rm.tags = ("filesystem", "file", "delete", "destructive")
    registry.register(fs_rm, aliases=["delete_file", "rm"])

    return registry
