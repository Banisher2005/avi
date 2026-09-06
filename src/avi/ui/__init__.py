"""AVI Desktop UI subsystem.

Public API::

    from avi.ui import AviApp, is_ui_available, check_display

    AviApp(router, config).run()
"""

from avi.ui.app import AviApp
from avi.ui.window import _GTK_AVAILABLE as _GTK_AVAILABLE
from avi.ui.window import check_display


def is_ui_available() -> bool:
    """Return True if GTK4 and a display server are available."""
    return _GTK_AVAILABLE and check_display()


__all__ = ["AviApp", "check_display", "is_ui_available"]
