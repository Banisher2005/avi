"""Unified Browser Controller for AVI.

Combines Chrome DevTools Protocol (CDP) direct control with OS-level window
and process fallbacks when remote debugging is unavailable.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

from avi.browser.cdp import CdpClient
from avi.browser.models import BrowserState

logger = logging.getLogger(__name__)

KNOWN_BROWSERS = ("google-chrome", "chrome", "firefox", "brave", "chromium")


class BrowserController:
    """Manages browser discovery, state observation, and hybrid interaction."""

    def __init__(
        self,
        cdp_port: int = 9222,
        cdp_host: str = "127.0.0.1",
        timeout: float = 1.0,
    ) -> None:
        self.cdp = CdpClient(host=cdp_host, port=cdp_port, timeout=timeout)
        self._last_navigated_url: str | None = None
        self._last_page_title: str | None = None

    def detect_running_browsers(self) -> list[str]:
        """Detect any installed browser processes currently running."""
        running = []
        try:
            # Check via /proc or pgrep
            res = subprocess.run(
                ["pgrep", "-a", "-l", "-f", "(google-chrome|chrome|firefox|brave|chromium)"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            out = res.stdout.lower()
            for b in KNOWN_BROWSERS:
                if b in out and b not in running:
                    running.append(b)
        except Exception as err:
            logger.debug("Failed checking running browsers: %s", err)

        return running

    def observe(self, window_list: list[dict[str, Any]] | None = None) -> BrowserState:
        """Observe current browser state across CDP and system desktop state."""
        state = BrowserState()
        running = self.detect_running_browsers()
        state.is_running = len(running) > 0
        if running:
            state.browser_name = running[0]

        # 1. Try CDP if available
        if self.cdp.is_available():
            state.cdp_connected = True
            tabs = self.cdp.list_tabs()
            state.tab_count = len(tabs)
            active = self.cdp.get_active_tab()
            if active:
                state.active_tab_id = active.get("id")
                state.url = active.get("url")
                state.title = active.get("title")
                state.status = "ready"
                self._last_navigated_url = state.url
                self._last_page_title = state.title
                return state

        # 2. Desktop Window Inspection fallback
        windows = window_list
        if windows is None:
            try:
                from avi.capabilities.desktop.window import WindowListCapability

                win_cap = WindowListCapability()
                res = win_cap.execute()
                if res.success and isinstance(res.data, dict):
                    windows = res.data.get("windows", [])
            except Exception as err:
                logger.debug("Window inspection error: %s", err)
                windows = []

        active_browser_win = None
        if windows:
            for w in windows:
                w_title = (w.get("title") or "").lower()
                w_class = (w.get("class") or "").lower()
                for b in KNOWN_BROWSERS:
                    if b in w_title or b in w_class:
                        active_browser_win = w
                        break
                if active_browser_win:
                    break

        if active_browser_win:
            state.is_active_window = True
            state.window_title = active_browser_win.get("title")
            state.title = state.window_title
            state.status = "ready"
            if not state.browser_name:
                state.browser_name = active_browser_win.get("class") or "browser"
        elif state.is_running:
            state.status = "ready"
            state.title = self._last_page_title
        else:
            state.status = "unavailable"

        if self._last_navigated_url and not state.url:
            state.url = self._last_navigated_url

        return state

    def record_navigation(self, url: str, title: str | None = None) -> None:
        """Record a newly navigated URL into internal tracker."""
        self._last_navigated_url = url
        if title:
            self._last_page_title = title
