"""Browser tab management capability."""

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

VALID_TAB_ACTIONS = {"list", "new", "switch", "activate", "close"}


class BrowserTabsCapability(BaseCapability):
    """Inspect and manage browser tabs (list, new, switch, close)."""

    name = "browser.tabs"
    description = (
        "Inspect and manage browser tabs. Actions: 'list' (list open tabs), "
        "'new' (open new tab at optional url), 'switch'/'activate' (switch to tab_id), "
        "or 'close' (close tab_id)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "new", "switch", "activate", "close"],
                "description": "The tab action to perform: 'list', 'new', 'switch', 'activate', or 'close'.",
                "default": "list",
            },
            "url": {
                "type": "string",
                "description": "Optional URL to open when creating a new tab (default 'about:blank').",
            },
            "tab_id": {
                "type": "string",
                "description": "Tab identifier to switch to or close.",
            },
        },
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(self, controller: BrowserController | None = None) -> None:
        self.controller = controller or BrowserController()
        self.tags = ("browser", "web", "tab", "tabs", "manage_tabs")
        self.aliases = ["tabs", "browser.tab", "manage_tabs", "browser_tabs"]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute tab management action."""
        action = str(kwargs.get("action", "list")).strip().lower()
        if action not in VALID_TAB_ACTIONS:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Invalid tab action '{action}'. Must be one of: {sorted(VALID_TAB_ACTIONS)}",
                message=f"Unknown tab action: {action}",
            )

        url = str(kwargs.get("url") or "about:blank")
        tab_id = kwargs.get("tab_id")

        if action == "list":
            tabs = self.controller.list_tabs()
            obs = self.controller.observe()
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=f"Found {len(tabs)} open browser tab(s).",
                data={
                    "action": "list",
                    "tabs": [t.to_dict() for t in tabs],
                    "active_tab_id": obs.active_tab_id,
                    "observation": obs.to_dict(),
                },
            )

        if action == "new":
            new_t = self.controller.new_tab(url=url)
            obs = self.controller.observe()
            msg = f"Opened new browser tab at '{url}'." if new_t else f"Requested new tab at '{url}'."
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=msg,
                data={
                    "action": "new",
                    "url": url,
                    "new_tab": new_t.to_dict() if new_t else None,
                    "tabs": [t.to_dict() for t in self.controller.list_tabs()],
                    "observation": obs.to_dict(),
                },
            )

        if action in ("switch", "activate"):
            if not tab_id:
                return CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error="tab_id parameter is required to switch tabs.",
                    message="Missing tab_id parameter.",
                )
            ok = self.controller.activate_tab(str(tab_id))
            obs = self.controller.observe()
            if ok:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    message=f"Switched to browser tab {tab_id}.",
                    data={
                        "action": "switch",
                        "tab_id": str(tab_id),
                        "tabs": [t.to_dict() for t in self.controller.list_tabs()],
                        "observation": obs.to_dict(),
                    },
                )
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Could not switch to tab {tab_id}.",
                message=f"Failed to activate tab {tab_id}.",
            )

        if action == "close":
            target_id = str(tab_id) if tab_id else self.controller.observe().active_tab_id
            if not target_id:
                return CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error="No active tab or tab_id specified to close.",
                    message="Missing tab_id to close.",
                )
            ok = self.controller.close_tab(target_id)
            obs = self.controller.observe()
            if ok:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    message=f"Closed browser tab {target_id}.",
                    data={
                        "action": "close",
                        "tab_id": target_id,
                        "tabs": [t.to_dict() for t in self.controller.list_tabs()],
                        "observation": obs.to_dict(),
                    },
                )
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Could not close tab {target_id}.",
                message=f"Failed to close tab {target_id}.",
            )

        return CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error=f"Unhandled action {action}",
            message=f"Unhandled tab action: {action}",
        )
