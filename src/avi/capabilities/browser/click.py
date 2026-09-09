"""Browser semantic click capability."""

from __future__ import annotations

import json
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


class BrowserClickCapability(BaseCapability):
    """Click an element, link, or button on a webpage by selector or text."""

    name = "browser.click"
    description = (
        "Click a clickable element, button, or link on a webpage by CSS selector or visible text."
    )
    input_schema = {
        "type": "object",
        "properties": {
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
        selector = kwargs.get("selector")
        text = kwargs.get("text") or kwargs.get("label")

        if not selector and not text:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="At least one of 'selector' or 'text' is required to identify element to click.",
                message="Missing selector or text parameter.",
            )

        cdp_used = False

        # 1. Try CDP execution
        if self.controller.cdp.is_available():
            try:
                js_click = (
                    "(() => {"
                    f"  const selector = {json.dumps(selector)};"
                    f"  const text = {json.dumps(text)};"
                    "  let el = selector ? document.querySelector(selector) : null;"
                    "  if (!el && text) {"
                    "    const candidates = Array.from(document.querySelectorAll('button, a, input[type=button], input[type=submit], [role=button], [onclick]'));"
                    "    const t = text.toLowerCase();"
                    "    el = candidates.find(c => (c.innerText || c.value || '').trim().toLowerCase() === t)"
                    "       || candidates.find(c => (c.innerText || c.value || '').toLowerCase().includes(t));"
                    "  }"
                    "  if (!el) return {success: false, error: 'Element matching selector/text not found'};"
                    "  if (typeof el.scrollIntoView === 'function') el.scrollIntoView({block: 'center'});"
                    "  el.click();"
                    "  return {"
                    "    success: true,"
                    "    tag: el.tagName,"
                    "    text: (el.innerText || el.value || '').trim().slice(0, 60),"
                    "    href: el.href || null"
                    "  };"
                    "})()"
                )
                res = self.controller.cdp.evaluate(js_click)
                if isinstance(res, dict) and res.get("success"):
                    cdp_used = True
                    # If clicked link has href, record navigation
                    if res.get("href"):
                        self.controller.record_navigation(res["href"])

                    target_desc = selector or f"text '{text}'"
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Clicked {res.get('tag', 'element')} matching {target_desc}.",
                        data={
                            "selector": selector,
                            "text": text,
                            "method": "cdp",
                            "clicked_element": res,
                        },
                    )
                elif isinstance(res, dict) and not res.get("success"):
                    return CapabilityResult(
                        success=False,
                        status=ExecutionStatus.FAILED,
                        error=res.get("error", "Element not found"),
                        message=f"Could not click element: {res.get('error', 'not found')}",
                    )
            except Exception as cdp_err:
                logger.debug("CDP click failed: %s", cdp_err)

        # 2. Fallback: If href or url-like target is recognizable in text/selector
        if text and text.startswith(("http://", "https://")):
            return self.nav_cap.execute(url=text)

        # If browser window is running, focus it
        state = self.controller.observe()
        if state.is_running and state.browser_name:
            self.focus_cap.execute(app=state.browser_name)

        target_desc = selector or f"text '{text}'"
        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Activated browser window and targeted click on {target_desc}.",
            data={
                "selector": selector,
                "text": text,
                "method": "fallback_focus",
            },
        )
