"""Desktop environment and display server detection for Linux."""

import os
from dataclasses import dataclass
from typing import Literal

DisplayServer = Literal["wayland", "x11", "headless"]


@dataclass
class DesktopEnvironmentInfo:
    """Detected Linux desktop and display subsystem details."""

    display_server: DisplayServer
    session_type: str
    desktop: str
    is_headless: bool
    can_grab_keys_directly: bool
    recommended_hotkey_method: str


def detect_desktop_environment() -> DesktopEnvironmentInfo:
    """Inspect environment variables to detect Linux display server and desktop manager."""
    wayland_display = os.getenv("WAYLAND_DISPLAY")
    x11_display = os.getenv("DISPLAY")
    session_type = os.getenv("XDG_SESSION_TYPE", "").lower()
    desktop = os.getenv("XDG_CURRENT_DESKTOP", "").lower()

    if wayland_display or session_type == "wayland":
        display_server: DisplayServer = "wayland"
        is_headless = False
        can_grab = False  # Wayland security architecture prevents arbitrary client keylogging
        method = "desktop_shortcut"  # Native compositor shortcut (GNOME/KDE/Sway)
    elif x11_display or session_type == "x11":
        display_server = "x11"
        is_headless = False
        can_grab = True
        method = "x11_listener"  # Direct key listener or desktop shortcut
    else:
        display_server = "headless"
        is_headless = True
        can_grab = False
        method = "headless"

    return DesktopEnvironmentInfo(
        display_server=display_server,
        session_type=session_type or display_server,
        desktop=desktop or "unknown",
        is_headless=is_headless,
        can_grab_keys_directly=can_grab,
        recommended_hotkey_method=method,
    )
