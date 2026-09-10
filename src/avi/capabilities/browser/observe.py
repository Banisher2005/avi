"""Browser observation capability."""

from __future__ import annotations

from typing import Any

from avi.browser.controller import BrowserController
from avi.capabilities.models import BaseCapability, CapabilityResult, ExecutionStatus
from avi.safety.models import ActionCategory


class BrowserObserveCapability(BaseCapability):
    """Observes current browser state, URL, title, and connection status."""

    name = "browser.observe"
    description = "Observe current browser state including active window, URL, page title, and status."
    risk_category = ActionCategory.READ_ONLY
    requires_confirmation = False
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, controller: BrowserController | None = None) -> None:
        self.controller = controller or BrowserController()
        self.tags = ("browser", "web", "observe", "state", "read_only")
        self.aliases = ["browser.state", "observe_browser", "get_browser_state"]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute browser state observation."""
        try:
            state = self.controller.observe()
            title_str = state.title or "No active page"
            browser_str = state.browser_name or "browser"
            msg = f"Browser state: {browser_str} ({state.status}) - {title_str}"
            data = state.to_dict()
            data["observation"] = dict(data)
            data["summary"] = state.formatted_summary()
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=msg,
                data=data,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to observe browser state: {err}",
            )
