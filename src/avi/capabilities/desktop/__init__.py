"""Desktop environment capabilities for AVI."""

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

__all__ = [
    "ClipboardGetCapability",
    "ClipboardSetCapability",
    "LaunchAppCapability",
    "MediaControlCapability",
    "NotificationCapability",
    "OpenDirectoryCapability",
    "OpenFileCapability",
    "OpenUrlCapability",
    "PressKeyCapability",
    "ScreenshotCapability",
    "TypeTextCapability",
    "VolumeGetCapability",
    "VolumeSetCapability",
    "WindowFocusCapability",
    "WindowListCapability",
]
