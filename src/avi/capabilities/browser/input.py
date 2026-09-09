"""Browser text input capability."""

from __future__ import annotations

import json
import logging
from typing import Any

from avi.browser.controller import BrowserController
from avi.capabilities.desktop.input import PressKeyCapability, TypeTextCapability
from avi.capabilities.desktop.window import WindowFocusCapability
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger(__name__)


class BrowserTypeCapability(BaseCapability):
    """Type text into an input field or active element in the browser."""

    name = "browser.type"
    description = (
        "Type text into a web input field, search box, or active element in the browser. "
        "Supports CSS selector targeting, field clearing, and pressing Enter."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text string to type into the browser input.",
            },
            "selector": {
                "type": "string",
                "description": "Optional CSS selector of the target input element (e.g. 'input[name=\"q\"]').",
            },
            "press_enter": {
                "type": "boolean",
                "description": "Whether to press Enter after typing the text (e.g. to submit a search).",
                "default": False,
            },
            "clear_existing": {
                "type": "boolean",
                "description": "Whether to clear existing text in the target field before typing.",
                "default": False,
            },
        },
        "required": ["text"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(
        self,
        controller: BrowserController | None = None,
        type_cap: TypeTextCapability | None = None,
        press_cap: PressKeyCapability | None = None,
        focus_cap: WindowFocusCapability | None = None,
    ) -> None:
        self.controller = controller or BrowserController()
        self.type_cap = type_cap or TypeTextCapability()
        self.press_cap = press_cap or PressKeyCapability()
        self.focus_cap = focus_cap or WindowFocusCapability()
        self.tags = ("browser", "web", "type", "input", "keyboard", "form")
        self.aliases = ["browser.input", "type_into_browser", "web.type"]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute text typing into browser."""
        text = kwargs.get("text")
        if text is None:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Text parameter is required for browser typing.",
                message="Missing required 'text' parameter.",
            )

        text_str = str(text)
        selector = kwargs.get("selector")
        press_enter = bool(kwargs.get("press_enter", False))
        clear_existing = bool(kwargs.get("clear_existing", False))

        cdp_used = False

        # 1. Try CDP if available
        if self.controller.cdp.is_available():
            try:
                js_script = (
                    "(() => {"
                    f"  const selector = {json.dumps(selector)};"
                    f"  const text = {json.dumps(text_str)};"
                    f"  const clear = {json.dumps(clear_existing)};"
                    f"  const pressEnter = {json.dumps(press_enter)};"
                    "  let el = selector ? document.querySelector(selector) : document.activeElement;"
                    "  if (!el && !selector) el = document.querySelector('input:not([type=hidden]), textarea');"
                    "  if (!el) return {success: false, error: 'Element not found'};"
                    "  el.focus();"
                    "  if (clear) el.value = '';"
                    "  el.value = clear ? text : (el.value + text);"
                    "  el.dispatchEvent(new Event('input', {bubbles: true}));"
                    "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                    "  if (pressEnter) {"
                    "    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));"
                    "    if (el.form) el.form.submit();"
                    "  }"
                    "  return {success: true, tag: el.tagName, id: el.id, name: el.name};"
                    "})()"
                )
                eval_res = self.controller.cdp.evaluate(js_script)
                if isinstance(eval_res, dict) and eval_res.get("success"):
                    cdp_used = True
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Typed '{text_str}' into {eval_res.get('tag', 'element')} via CDP.",
                        data={
                            "text": text_str,
                            "selector": selector,
                            "press_enter": press_enter,
                            "clear_existing": clear_existing,
                            "method": "cdp",
                            "element_info": eval_res,
                        },
                    )
            except Exception as cdp_err:
                logger.debug("CDP typing failed, falling back to desktop input: %s", cdp_err)

        # 2. Desktop input fallback
        # Focus browser window if a browser is known to be running
        state = self.controller.observe()
        if state.is_running and state.browser_name:
            self.focus_cap.execute(app=state.browser_name)

        if clear_existing:
            self.press_cap.execute(key="ctrl+a")
            self.press_cap.execute(key="BackSpace")

        type_res = self.type_cap.execute(text=text_str)
        if not type_res.success:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=type_res.error,
                message=f"Failed to type text via desktop input: {type_res.message}",
            )

        if press_enter:
            self.press_cap.execute(key="Return")

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Typed '{text_str}' into browser window.",
            data={
                "text": text_str,
                "selector": selector,
                "press_enter": press_enter,
                "clear_existing": clear_existing,
                "method": "desktop_input",
            },
        )
