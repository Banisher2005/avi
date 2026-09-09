"""Browser navigation capability."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from avi.actions.system import OpenUrlAction
from avi.browser.controller import BrowserController
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger(__name__)

DISALLOWED_SCHEMES = {"javascript", "data", "file", "vbscript", "about"}


def sanitize_and_validate_url(raw_url: str) -> str:
    """Validate and sanitize URL, prepending https:// if scheme is missing."""
    url = raw_url.strip()
    if not url:
        raise ValueError("URL cannot be empty.")

    parsed = urllib.parse.urlparse(url)

    # If no scheme was provided (e.g. "github.com/repo"), prepend https://
    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urllib.parse.urlparse(url)

    scheme = parsed.scheme.lower()
    if scheme in DISALLOWED_SCHEMES:
        raise ValueError(f"Navigation to '{scheme}:' URLs is disallowed for safety.")

    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{scheme}'. Only HTTP and HTTPS are permitted.")

    if not parsed.netloc:
        raise ValueError("URL must include a valid host or domain name.")

    return url


class BrowserNavigateCapability(BaseCapability):
    """Navigate browser to an HTTP/HTTPS web address."""

    name = "browser.navigate"
    description = "Navigate browser to a specified HTTP or HTTPS web address."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The destination URL to navigate to (e.g. 'https://github.com')",
            },
            "new_tab": {
                "type": "boolean",
                "description": "Whether to open in a new tab if supported",
                "default": False,
            },
        },
        "required": ["url"],
    }
    risk_category = ActionCategory.EXTERNAL_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(self, controller: BrowserController | None = None) -> None:
        self.controller = controller or BrowserController()
        self.tags = ("browser", "web", "navigate", "url", "open")
        self.aliases = ["navigate", "browser.go", "goto_url", "browser.open"]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute navigation to target URL."""
        raw_url = kwargs.get("url") or kwargs.get("address") or kwargs.get("link")
        if not raw_url:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="URL parameter is required for browser navigation.",
                message="Missing URL parameter.",
            )

        try:
            target_url = sanitize_and_validate_url(str(raw_url))
        except ValueError as val_err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(val_err),
                message=f"Invalid URL: {val_err}",
            )

        new_tab = bool(kwargs.get("new_tab", False))
        cdp_used = False

        # Attempt CDP navigation if active
        if self.controller.cdp.is_available():
            try:
                if new_tab:
                    tab_info = self.controller.cdp.new_tab(target_url)
                    cdp_used = tab_info is not None
                else:
                    cdp_used = self.controller.cdp.navigate(target_url)
            except Exception as cdp_err:
                logger.debug("CDP navigation fallback triggered: %s", cdp_err)
                cdp_used = False

        # Fallback to system browser launch
        if not cdp_used:
            act = OpenUrlAction(target_url)
            act_res = act.execute()
            if not act_res.success:
                return CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error=act_res.message,
                    message=f"Failed to open URL in browser: {act_res.message}",
                )

        parsed = urllib.parse.urlparse(target_url)
        self.controller.record_navigation(target_url)

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Successfully navigated to {target_url}",
            data={
                "url": target_url,
                "scheme": parsed.scheme,
                "host": parsed.netloc,
                "path": parsed.path,
                "method": "cdp" if cdp_used else "system_browser",
                "cdp_used": cdp_used,
            },
        )
