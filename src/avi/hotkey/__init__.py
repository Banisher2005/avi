"""Optional Linux global hotkey and desktop integration subsystem."""

from avi.hotkey.detector import DesktopEnvironmentInfo, detect_desktop_environment
from avi.hotkey.service import (
    generate_desktop_entry,
    generate_systemd_user_service,
    get_hotkey_instructions,
)

__all__ = [
    "DesktopEnvironmentInfo",
    "detect_desktop_environment",
    "generate_desktop_entry",
    "generate_systemd_user_service",
    "get_hotkey_instructions",
]
