"""Application discovery and resolution subsystem for AVI.

Discovers installed applications on Linux using:
1. Standard desktop entries (.desktop files) across XDG application paths
2. System executable PATH inspection
3. Natural-language alias dictionary for common desktop applications
"""

import configparser
import os
import platform
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from avi.apps.models import ApplicationResolution

# Standard desktop entry directories in search order
DESKTOP_ENTRY_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
]

# Field codes to strip from .desktop Exec lines (e.g., %u, %U, %f, %F)
_EXEC_FIELD_CODE_RE = re.compile(r"%[a-zA-Z]")

# Common application aliases to canonical search terms
DEFAULT_ALIASES: dict[str, list[str]] = {
    "chrome": ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"],
    "google chrome": ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"],
    "chromium": ["chromium-browser", "chromium"],
    "brave": ["brave-origin", "brave-origin-stable", "brave-browser", "brave"],
    "brave browser": ["brave-origin", "brave-origin-stable", "brave-browser", "brave"],
    "firefox": ["firefox", "firefox-esr"],
    "firefox browser": ["firefox", "firefox-esr"],
    "antigravity": ["agy", "antigravity"],
    "agy": ["agy"],
    "code": ["code", "codium", "code-oss"],
    "vscode": ["code", "codium", "code-oss"],
    "vs code": ["code", "codium", "code-oss"],
    "visual studio code": ["code", "codium", "code-oss"],
    "terminal": ["gnome-terminal", "x-terminal-emulator", "alacritty", "kitty", "konsole", "xterm"],
    "files": ["nautilus", "thunar", "dolphin", "pcmanfm"],
    "file manager": ["nautilus", "thunar", "dolphin", "pcmanfm"],
    "nautilus": ["nautilus"],
    "calculator": ["gnome-calculator", "kcalc", "xcalc"],
    "text editor": ["gedit", "gnome-text-editor", "kate", "mousepad"],
    "editor": ["gedit", "gnome-text-editor", "kate", "mousepad"],
    "vlc": ["vlc"],
    "spotify": ["spotify"],
    "slack": ["slack"],
    "discord": ["discord"],
    "gimp": ["gimp"],
    "obs": ["obs"],
}


class ApplicationResolver:
    """Discovers and resolves applications installed on the system."""

    def __init__(
        self,
        desktop_dirs: Sequence[Path] | None = None,
        aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self.desktop_dirs = list(desktop_dirs) if desktop_dirs is not None else DESKTOP_ENTRY_DIRS
        self.aliases = dict(aliases) if aliases is not None else DEFAULT_ALIASES
        self._desktop_cache: dict[str, dict[str, str]] | None = None

    def _clean_exec_command(self, raw_exec: str) -> str:
        """Strip XDG desktop field codes like %U, %u, %F from Exec command."""
        cleaned = _EXEC_FIELD_CODE_RE.sub("", raw_exec).strip()
        tokens = shlex.split(cleaned) if cleaned else []
        return tokens[0] if tokens else ""

    def _index_desktop_entries(self) -> dict[str, dict[str, str]]:
        """Scan and index installed .desktop application entries."""
        if self._desktop_cache is not None:
            return self._desktop_cache

        entries: dict[str, dict[str, str]] = {}

        for directory in self.desktop_dirs:
            if not directory.is_dir():
                continue
            for file_path in directory.glob("*.desktop"):
                try:
                    parser = configparser.ConfigParser(interpolation=None, strict=False)
                    parser.read(file_path, encoding="utf-8")
                    if not parser.has_section("Desktop Entry"):
                        continue
                    section = parser["Desktop Entry"]

                    # Skip hidden or nondisplayed entries
                    if section.get("NoDisplay", "false").lower() == "true":
                        continue
                    if section.get("Hidden", "false").lower() == "true":
                        continue

                    name = section.get("Name", "").strip()
                    exec_cmd = section.get("Exec", "").strip()
                    generic_name = section.get("GenericName", "").strip()

                    if not name or not exec_cmd:
                        continue

                    binary = self._clean_exec_command(exec_cmd)
                    if not binary:
                        continue

                    info = {
                        "name": name,
                        "generic_name": generic_name,
                        "exec": binary,
                        "raw_exec": exec_cmd,
                        "path": str(file_path),
                    }

                    # Index by lower-case filename stem and application name
                    stem_key = file_path.stem.lower()
                    name_key = name.lower()

                    if stem_key not in entries:
                        entries[stem_key] = info
                    if name_key not in entries:
                        entries[name_key] = info

                except Exception:
                    continue

        self._desktop_cache = entries
        return entries

    def normalize_app_name(self, query: str) -> str:
        """Extract canonical application name from natural-language query."""
        s = query.strip().lower()
        # Strip common action prefixes
        for prefix in (
            "open the application",
            "open the app",
            "open application",
            "open app",
            "open",
            "launch",
            "start",
            "run",
        ):
            if s.startswith(prefix + " "):
                s = s[len(prefix) :].strip()
                break

        # Strip common trailing noise
        s = re.sub(r"\s+(?:application|app|browser)$", "", s).strip()
        return s

    def resolve(self, query: str) -> ApplicationResolution:
        """Resolve a requested application name to an executable."""
        normalized = self.normalize_app_name(query)
        current_platform = platform.system()

        # 1. Check direct aliases
        candidates: list[str] = []
        if normalized in self.aliases:
            candidates.extend(self.aliases[normalized])

        # Also add normalized name itself
        if normalized not in candidates:
            candidates.append(normalized)

        # 2. Check desktop entry cache
        desktop_entries = self._index_desktop_entries()

        # A: Direct match in desktop entries
        for key, info in desktop_entries.items():
            if normalized == key or normalized in info["name"].lower():
                binary = info["exec"]
                full_path = shutil.which(binary) or (
                    binary if os.path.isabs(binary) and os.access(binary, os.X_OK) else None
                )
                if full_path:
                    return ApplicationResolution(
                        requested_name=query,
                        canonical_name=info["name"],
                        executable=full_path,
                        desktop_entry=info["path"],
                        platform=current_platform,
                        installed=True,
                        confidence=0.95,
                    )

        # B: Check alias candidate binaries against PATH and desktop files
        for cand in candidates:
            # Check PATH
            found_which = shutil.which(cand)
            if found_which:
                # Format friendly display name
                canonical = cand.replace("-", " ").title()
                return ApplicationResolution(
                    requested_name=query,
                    canonical_name=canonical,
                    executable=found_which,
                    desktop_entry=None,
                    platform=current_platform,
                    installed=True,
                    confidence=0.9,
                )

            # Check desktop entry names starting with candidate
            for key, info in desktop_entries.items():
                if cand in key or cand in info["exec"].lower():
                    binary = info["exec"]
                    full_path = shutil.which(binary) or (
                        binary if os.path.isabs(binary) and os.access(binary, os.X_OK) else None
                    )
                    if full_path:
                        return ApplicationResolution(
                            requested_name=query,
                            canonical_name=info["name"],
                            executable=full_path,
                            desktop_entry=info["path"],
                            platform=current_platform,
                            installed=True,
                            confidence=0.85,
                        )

        # Not installed / not found
        return ApplicationResolution(
            requested_name=query,
            canonical_name=normalized.title(),
            executable=None,
            desktop_entry=None,
            platform=current_platform,
            installed=False,
            confidence=0.0,
        )

    def launch(
        self,
        resolution: ApplicationResolution,
        extra_args: Sequence[str] | None = None,
    ) -> tuple[bool, str]:
        """Launch the resolved application in an isolated subprocess.

        Security invariant: strictly uses subprocess.Popen(..., shell=False).
        """
        if not resolution.is_resolved or not resolution.executable:
            return (
                False,
                f"Application '{resolution.canonical_name}' is not installed or executable not found.",
            )

        cmd = [resolution.executable]
        if extra_args:
            cmd.extend(extra_args)

        try:
            # Launch detached from current terminal process group
            subprocess.Popen(
                cmd,
                shell=False,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True, f"Opening {resolution.canonical_name}."
        except Exception as err:
            return False, f"Failed to launch {resolution.canonical_name}: {err}"
