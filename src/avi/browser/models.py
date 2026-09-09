"""Data models for AVI browser computer use."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrowserState:
    """Represents the observed state of the browser."""

    url: str | None = None
    title: str | None = None
    browser_name: str | None = None
    window_title: str | None = None
    is_running: bool = False
    is_active_window: bool = False
    cdp_connected: bool = False
    status: str = "idle"  # "idle", "loading", "ready", "unavailable"
    tab_count: int = 0
    active_tab_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert state to serializable dictionary."""
        return {
            "url": self.url,
            "title": self.title,
            "browser_name": self.browser_name,
            "window_title": self.window_title,
            "is_running": self.is_running,
            "is_active_window": self.is_active_window,
            "cdp_connected": self.cdp_connected,
            "status": self.status,
            "tab_count": self.tab_count,
            "active_tab_id": self.active_tab_id,
            "metadata": dict(self.metadata),
        }
