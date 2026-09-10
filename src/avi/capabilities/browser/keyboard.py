"""Browser keyboard key press capability."""

from __future__ import annotations

import logging
from typing import Any

from avi.browser.controller import BrowserController
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger(__name__)


class BrowserPressKeyCapability(BaseCapability):
    """Send a keyboard key press to the active browser element or window."""

    name = "browser.press_key"
    description = (
        "Send a keyboard key press (such as 'Enter', 'Escape', 'Tab', 'ArrowDown', "
        "'ArrowUp', 'Space', 'Backspace') to the active element or window in the browser."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The key name to press (e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'Space', 'Backspace').",
            },
        },
        "required": ["key"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(self, controller: BrowserController | None = None) -> None:
        self.controller = controller or BrowserController()
        self.tags = ("browser", "web", "keyboard", "key", "press", "input")
        self.aliases = ["browser.key", "press_browser_key", "web.press_key"]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute key press in browser."""
        key = kwargs.get("key")
        if not key:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Key parameter is required for browser key press.",
                message="Missing required 'key' parameter.",
            )

        key_str = str(key).strip()
        res = self.controller.press_key(key=key_str)

        if not res.get("success"):
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=res.get("error", f"Failed to press key '{key_str}'"),
                message=f"Failed to press key '{key_str}' in browser.",
            )

        data: dict[str, Any] = {
            "key": key_str,
            "method": res.get("method", "cdp"),
        }
        if "observation" in res:
            data["observation"] = res["observation"]

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Pressed '{key_str}' in browser.",
            data=data,
        )
