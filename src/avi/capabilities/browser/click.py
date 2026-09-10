"""Browser semantic click capability."""

from __future__ import annotations

import logging
from typing import Any

from avi.browser.controller import BrowserController
from avi.capabilities.browser.navigate import BrowserNavigateCapability
from avi.capabilities.desktop.window import WindowFocusCapability
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger(__name__)


HIGH_RISK_CLICK_TERMS = {
    "buy now",
    "purchase",
    "submit payment",
    "pay now",
    "pay",
    "delete account",
    "confirm purchase",
    "place order",
    "checkout",
    "transfer funds",
    "confirm payment",
}


class BrowserClickCapability(BaseCapability):
    """Click an element, link, or button on a webpage by element_id, selector, or text."""

    name = "browser.click"
    description = (
        "Click a clickable element, button, or link on a webpage by element_id, CSS selector, or visible text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "element_id": {
                "type": ["integer", "string"],
                "description": "Observation-local ID of the interactive element to click (e.g. 1, 2, '3').",
            },
            "selector": {
                "type": "string",
                "description": "Optional CSS selector of the element to click (e.g. 'button.primary', '#submit').",
            },
            "text": {
                "type": "string",
                "description": "Optional visible text or label of the link/button to click (e.g. 'Next', 'Log In').",
            },
            "wait_navigation": {
                "type": "boolean",
                "description": "Whether to observe and record potential URL changes after clicking.",
                "default": False,
            },
            "confirmed": {
                "type": "boolean",
                "description": "Explicit confirmation for high-risk actions (e.g. purchase, payments).",
                "default": False,
            },
        },
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(
        self,
        controller: BrowserController | None = None,
        nav_cap: BrowserNavigateCapability | None = None,
        focus_cap: WindowFocusCapability | None = None,
    ) -> None:
        self.controller = controller or BrowserController()
        self.nav_cap = nav_cap or BrowserNavigateCapability(controller=self.controller)
        self.focus_cap = focus_cap or WindowFocusCapability()
        self.tags = ("browser", "web", "click", "element", "button", "link")
        self.aliases = ["click_element", "browser.click_element", "web.click"]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute element click in browser."""
        element_id = kwargs.get("element_id") or kwargs.get("id")
        selector = kwargs.get("selector")
        text = kwargs.get("text") or kwargs.get("label")
        confirmed = bool(kwargs.get("confirmed", False))

        if element_id is None and not selector and not text:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="At least one of 'element_id', 'selector', or 'text' is required to identify element to click.",
                message="Missing element_id, selector, or text parameter.",
            )

        # Safety Check: Inspect target element descriptor for high-risk operations
        target_descriptor = str(text or selector or "").lower()
        if element_id is not None:
            resolved_el, _ = self.controller.resolve_element(element_id=element_id)
            if resolved_el:
                target_descriptor += f" {resolved_el.text} {resolved_el.selector} {resolved_el.name or ''}".lower()

        if not confirmed:
            for term in HIGH_RISK_CLICK_TERMS:
                if term in target_descriptor:
                    return CapabilityResult(
                        success=False,
                        status=ExecutionStatus.CONFIRMATION_REQUIRED,
                        error=f"Click target contains high-risk action ('{term}').",
                        message=f"Action '{target_descriptor.strip()}' may be consequential or irreversible. Confirmation required.",
                        data={
                            "element_id": element_id,
                            "selector": selector,
                            "text": text,
                            "high_risk_term": term,
                            "confirmation_required": True,
                        },
                    )

        # Fallback if text is direct URL
        if text and text.startswith(("http://", "https://")):
            return self.nav_cap.execute(url=text)

        wait_seconds = 0.5 if kwargs.get("wait_navigation") else 0.3
        res = self.controller.click(
            element_id=element_id,
            selector=selector,
            text=text,
            wait_seconds=wait_seconds,
        )

        if not res.get("success"):
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=res.get("error", "Element not found"),
                message=f"Could not click element: {res.get('error', 'not found')}",
            )

        clicked_info = res.get("clicked_element", {})
        tag_str = clicked_info.get("tag") or "element"
        target_desc = (
            f"element #{element_id}"
            if element_id is not None
            else (selector or f"text '{text}'")
        )
        msg = f"Clicked {tag_str} matching {target_desc}."

        result_data: dict[str, Any] = {
            "element_id": element_id,
            "selector": selector,
            "text": text,
            "method": res.get("method", "cdp"),
            "clicked_element": clicked_info,
        }
        if "observation" in res:
            result_data["observation"] = res["observation"]

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=msg,
            data=result_data,
        )
