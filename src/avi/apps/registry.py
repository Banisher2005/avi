"""Dynamic Application Registry and discovery subsystem for AVI.

Discovers applications installed on the system using:
1. Standard desktop entries (.desktop files) across XDG application directories
2. System PATH inspection
3. Standard aliases for canonical applications
4. Real application icons (icon theme lookup or image file path; no emojis)
5. Structured application metadata, actions, and search keywords
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

logger = logging.getLogger("avi.apps.registry")

# Standard XDG application search directories in precedence order
STANDARD_DESKTOP_DIRS: list[Path] = [
    Path.home() / ".local/share/applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
]

# Field codes to strip from .desktop Exec lines (e.g. %u, %U, %f, %F, %k, %i)
_EXEC_FIELD_CODE_RE = re.compile(r"%[a-zA-Z]")

# Standard aliases mapping alias name -> canonical app identifier
STANDARD_APP_ALIASES: dict[str, list[str]] = {
    "chrome": ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"],
    "google chrome": ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"],
    "chromium": ["chromium-browser", "chromium"],
    "brave": ["brave-origin", "brave-origin-stable", "brave-browser", "brave"],
    "brave browser": ["brave-origin", "brave-origin-stable", "brave-browser", "brave"],
    "firefox": ["firefox", "firefox-esr"],
    "firefox browser": ["firefox", "firefox-esr"],
    "browser": ["google-chrome-stable", "google-chrome", "brave-browser", "firefox", "chromium"],
    "code": ["code", "codium", "code-oss"],
    "vscode": ["code", "codium", "code-oss"],
    "vs code": ["code", "codium", "code-oss"],
    "visual studio code": ["code", "codium", "code-oss"],
    "terminal": ["gnome-terminal", "x-terminal-emulator", "alacritty", "kitty", "konsole", "xterm"],
    "files": ["nautilus", "thunar", "dolphin", "pcmanfm"],
    "file manager": ["nautilus", "thunar", "dolphin", "pcmanfm"],
    "explorer": ["nautilus", "thunar", "dolphin", "pcmanfm"],
    "nautilus": ["nautilus"],
    "calculator": ["gnome-calculator", "kcalc", "xcalc"],
    "calc": ["gnome-calculator", "kcalc", "xcalc"],
    "text editor": ["gedit", "gnome-text-editor", "kate", "mousepad"],
    "editor": ["gedit", "gnome-text-editor", "kate", "mousepad"],
    "vlc": ["vlc"],
    "spotify": ["spotify"],
    "slack": ["slack"],
    "discord": ["discord"],
    "gimp": ["gimp"],
    "obs": ["obs", "obs-studio"],
    "antigravity": ["agy", "antigravity"],
    "agy": ["agy"],
}

# Standard category to icon fallback mappings (real icon theme names, never emoji)
CATEGORY_FALLBACK_ICONS: dict[str, str] = {
    "AudioVideo": "multimedia-player",
    "Audio": "audio-x-generic",
    "Video": "video-x-generic",
    "Development": "applications-development",
    "Education": "applications-education",
    "Game": "applications-games",
    "Graphics": "applications-graphics",
    "Network": "applications-internet",
    "Office": "applications-office",
    "Science": "applications-science",
    "Settings": "preferences-system",
    "System": "applications-system",
    "Utility": "applications-utilities",
}

DEFAULT_APP_ICON = "application-x-executable"


@dataclass
class AppAction:
    """Action that can be performed on an application."""

    id: str  # e.g. "launch", "focus", "close"
    name: str  # e.g. "Open application", "Bring to front", "Quit application"
    description: str
    is_default: bool = False


@dataclass
class ApplicationEntry:
    """Structured representation of an installed application."""

    id: str
    canonical_name: str
    display_name: str
    executable: str
    desktop_file: str | None = None
    icon: str = DEFAULT_APP_ICON
    comment: str = ""
    generic_name: str = ""
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    actions: list[AppAction] = field(default_factory=list)

    @property
    def searchable_terms(self) -> list[str]:
        """All terms that should match this application in fuzzy search."""
        terms = [
            self.canonical_name.lower(),
            self.display_name.lower(),
            Path(self.executable).name.lower(),
        ]
        for a in self.aliases:
            terms.append(a.lower())
        for k in self.keywords:
            terms.append(k.lower())
        if self.generic_name:
            terms.append(self.generic_name.lower())
        return list(dict.fromkeys(terms))


class ApplicationRegistry:
    """Dynamic discovery and registry for installed applications on the system."""

    _instance: ApplicationRegistry | None = None

    def __init__(
        self,
        desktop_dirs: Sequence[Path] | None = None,
        aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self.desktop_dirs = (
            list(desktop_dirs) if desktop_dirs is not None else list(STANDARD_DESKTOP_DIRS)
        )
        self.aliases = dict(aliases) if aliases is not None else dict(STANDARD_APP_ALIASES)
        self._applications: dict[str, ApplicationEntry] = {}
        self._alias_map: dict[str, str] = {}
        self._discovered = False

    @classmethod
    def get_instance(cls) -> ApplicationRegistry:
        """Singleton accessor for global application registry."""
        if cls._instance is None:
            cls._instance = ApplicationRegistry()
        return cls._instance

    @staticmethod
    def _clean_exec_command(raw_exec: str) -> str:
        """Clean field codes like %U, %f from Exec line and return primary binary."""
        cleaned = _EXEC_FIELD_CODE_RE.sub("", raw_exec).strip()
        tokens = shlex.split(cleaned) if cleaned else []
        return tokens[0] if tokens else ""

    def _resolve_icon(self, icon_str: str, categories: list[str]) -> str:
        """Resolve icon string from desktop entry or determine appropriate icon name.

        Never returns emojis. Returns icon theme name or absolute file path.
        """
        icon_str = icon_str.strip()
        if not icon_str:
            for cat in categories:
                if cat in CATEGORY_FALLBACK_ICONS:
                    return CATEGORY_FALLBACK_ICONS[cat]
            return DEFAULT_APP_ICON

        # Absolute file path check
        if os.path.isabs(icon_str):
            if Path(icon_str).exists():
                return icon_str
            # Check if file exists with .png or .svg extension
            for ext in (".png", ".svg", ".xpm"):
                p = Path(f"{icon_str}{ext}")
                if p.exists():
                    return str(p)

        # Standard icon theme name (e.g. google-chrome, accessories-calculator)
        return icon_str

    def discover(self, force: bool = False) -> dict[str, ApplicationEntry]:
        """Discover installed applications on system, populating registry cache."""
        if self._discovered and not force:
            return self._applications

        apps: dict[str, ApplicationEntry] = {}
        seen_executables: set[str] = set()

        # 1. Parse .desktop files
        for d in self.desktop_dirs:
            if not d.is_dir():
                continue
            for desktop_path in sorted(d.glob("*.desktop")):
                try:
                    parser = configparser.ConfigParser(interpolation=None, strict=False)
                    parser.read(desktop_path, encoding="utf-8")
                    if not parser.has_section("Desktop Entry"):
                        continue
                    section = parser["Desktop Entry"]

                    # Skip hidden or nondisplayed entries
                    if section.get("NoDisplay", "false").lower() == "true":
                        continue
                    if section.get("Hidden", "false").lower() == "true":
                        continue

                    name = section.get("Name", "").strip()
                    raw_exec = section.get("Exec", "").strip()
                    if not name or not raw_exec:
                        continue

                    binary = self._clean_exec_command(raw_exec)
                    if not binary:
                        continue

                    resolved_path = shutil.which(binary)
                    if not resolved_path and os.path.isabs(binary) and os.access(binary, os.X_OK):
                        resolved_path = binary

                    if not resolved_path:
                        continue

                    # Avoid duplicate entries for same executable path
                    if resolved_path in seen_executables:
                        continue
                    seen_executables.add(resolved_path)

                    generic_name = section.get("GenericName", "").strip()
                    comment = section.get("Comment", "").strip()
                    raw_categories = section.get("Categories", "").strip()
                    categories = [c.strip() for c in raw_categories.split(";") if c.strip()]
                    raw_keywords = section.get("Keywords", "").strip()
                    keywords = [k.strip() for k in raw_keywords.split(";") if k.strip()]

                    icon_raw = section.get("Icon", "").strip()
                    icon = self._resolve_icon(icon_raw, categories)

                    app_id = desktop_path.stem.lower()

                    # Define default actions
                    actions = [
                        AppAction(
                            id="launch",
                            name=f"Open {name}",
                            description=comment or f"Launch {name}",
                            is_default=True,
                        ),
                        AppAction(
                            id="focus",
                            name=f"Focus {name}",
                            description=f"Switch to running {name} window",
                            is_default=False,
                        ),
                        AppAction(
                            id="close",
                            name=f"Close {name}",
                            description=f"Close {name} application",
                            is_default=False,
                        ),
                    ]

                    entry = ApplicationEntry(
                        id=app_id,
                        canonical_name=name.lower(),
                        display_name=name,
                        executable=resolved_path,
                        desktop_file=str(desktop_path),
                        icon=icon,
                        comment=comment,
                        generic_name=generic_name,
                        categories=categories,
                        keywords=keywords,
                        actions=actions,
                    )
                    apps[app_id] = entry
                except Exception as exc:
                    logger.debug("Failed parsing %s: %s", desktop_path, exc)
                    continue

        # 2. Add aliases to discovered applications
        for alias, targets in self.aliases.items():
            alias_lower = alias.lower()
            for target in targets:
                target_lower = target.lower()
                for app in apps.values():
                    if (
                        target_lower == app.id
                        or target_lower == app.canonical_name
                        or target_lower == Path(app.executable).name.lower()
                    ):
                        if alias_lower not in app.aliases:
                            app.aliases.append(alias_lower)
                        self._alias_map[alias_lower] = app.id
                        break

        self._applications = apps
        self._discovered = True
        return self._applications

    def refresh(self) -> dict[str, ApplicationEntry]:
        """Invalidate cache and rediscover all applications."""
        self._applications.clear()
        self._alias_map.clear()
        self._discovered = False
        return self.discover(force=True)

    def invalidate(self) -> None:
        """Clear discovery cache without immediate rediscovery."""
        self._applications.clear()
        self._alias_map.clear()
        self._discovered = False

    def get_by_id(self, app_id: str) -> ApplicationEntry | None:
        """Retrieve application by unique ID."""
        self.discover()
        return self._applications.get(app_id.lower())

    def get_by_name_or_alias(self, name: str) -> ApplicationEntry | None:
        """Find an application by its display name, canonical name, or registered alias."""
        self.discover()
        clean = name.strip().lower()

        # 1. Alias map direct lookup
        if clean in self._alias_map:
            target_id = self._alias_map[clean]
            if target_id in self._applications:
                return self._applications[target_id]

        # 2. Exact match on app id or canonical name
        if clean in self._applications:
            return self._applications[clean]

        for app in self._applications.values():
            if clean == app.canonical_name or clean == app.display_name.lower():
                return app
            if clean == Path(app.executable).name.lower():
                return app
            if clean in app.aliases:
                return app

        return None

    def list_all(self) -> list[ApplicationEntry]:
        """List all discovered applications."""
        self.discover()
        return list(self._applications.values())

    def launch(
        self,
        app: ApplicationEntry | str,
        extra_args: Sequence[str] | None = None,
    ) -> tuple[bool, str]:
        """Launch application safely in detached process group.

        Security invariant: strictly uses subprocess.Popen(..., shell=False).
        """
        entry: ApplicationEntry | None
        if isinstance(app, str):
            entry = self.get_by_name_or_alias(app)
            if entry is None:
                return False, f"Application '{app}' not found."
        else:
            entry = app

        if not entry.executable or not shutil.which(entry.executable):
            return False, f"Executable for '{entry.display_name}' is not accessible."

        cmd = [entry.executable]
        if extra_args:
            cmd.extend(extra_args)

        try:
            subprocess.Popen(
                cmd,
                shell=False,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True, f"Launched {entry.display_name}."
        except Exception as err:
            logger.error("Failed launching application %s: %s", entry.display_name, err)
            return False, f"Failed to launch {entry.display_name}: {err}"

    def focus(self, app: ApplicationEntry | str) -> tuple[bool, str]:
        """Attempt to focus an existing window of this application."""
        entry: ApplicationEntry | None = (
            self.get_by_name_or_alias(app) if isinstance(app, str) else app
        )
        if entry is None:
            return False, f"Application '{app}' not found."

        # Check wmctrl
        wmctrl = shutil.which("wmctrl")
        if not wmctrl:
            # Fallback to launching
            return self.launch(entry)

        try:
            proc = subprocess.run(
                [wmctrl, "-l"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            title_pattern = entry.display_name.lower()
            for line in proc.stdout.splitlines():
                if title_pattern in line.lower():
                    win_id = line.split()[0]
                    subprocess.run(
                        [wmctrl, "-i", "-a", win_id],
                        capture_output=True,
                        timeout=2.0,
                    )
                    return True, f"Focused {entry.display_name} window."

            # If no window found, launch it
            return self.launch(entry)
        except Exception as err:
            return False, f"Could not focus {entry.display_name}: {err}"

    def close(self, app: ApplicationEntry | str) -> tuple[bool, str]:
        """Attempt to close windows of this application."""
        entry: ApplicationEntry | None = (
            self.get_by_name_or_alias(app) if isinstance(app, str) else app
        )
        if entry is None:
            return False, f"Application '{app}' not found."

        wmctrl = shutil.which("wmctrl")
        if wmctrl:
            try:
                proc = subprocess.run(
                    [wmctrl, "-l"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                closed_any = False
                for line in proc.stdout.splitlines():
                    if entry.display_name.lower() in line.lower():
                        win_id = line.split()[0]
                        subprocess.run(
                            [wmctrl, "-i", "-c", win_id],
                            capture_output=True,
                            timeout=2.0,
                        )
                        closed_any = True
                if closed_any:
                    return True, f"Closed {entry.display_name} window."
            except Exception:
                pass

        # Fallback to pkill if safe
        pkill = shutil.which("pkill")
        if pkill:
            bin_name = Path(entry.executable).name
            # Never pkill critical system binaries
            if bin_name not in ("systemd", "init", "bash", "sh", "python", "python3", "Xorg", "wayland"):
                try:
                    subprocess.run([pkill, "-f", bin_name], capture_output=True, timeout=2.0)
                    return True, f"Closed {entry.display_name} process."
                except Exception as err:
                    return False, f"Failed to close {entry.display_name}: {err}"

        return False, f"Could not close {entry.display_name}."
