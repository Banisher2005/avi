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
        "Supports element_id targeting, CSS selector targeting, field clearing, and pressing Enter."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text string to type into the browser input.",
            },
            "element_id": {
                "type": ["integer", "string"],
                "description": "Observation-local ID of the interactive element to type into.",
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
        element_id = kwargs.get("element_id") or kwargs.get("id")
        selector = kwargs.get("selector")
        press_enter = bool(kwargs.get("press_enter", False))
        clear_existing = bool(kwargs.get("clear_existing", False))

        # Resolve selector if element_id given
        if element_id is not None and not selector:
            resolved_el, resolved_sel = self.controller.resolve_element(element_id=element_id)
            if resolved_sel:
                selector = resolved_sel

        # 1. Try CDP if available
        if self.controller.cdp.is_available():
            try:
                js_script = (
                    "(() => {"
                    f"  const selector = {json.dumps(selector)};"
                    f"  const aviId = {json.dumps(int(element_id) if element_id is not None and str(element_id).isdigit() else None)};"
                    f"  const text = {json.dumps(text_str)};"
                    f"  const clear = {json.dumps(clear_existing)};"
                    f"  const pressEnter = {json.dumps(press_enter)};"
                    "  let el = null;"
                    "  if (aviId) el = document.querySelector(`[data-avi-id='${aviId}']`);"
                    "  if (!el && selector) el = document.querySelector(selector);"
                    "  if (!el && !aviId && !selector) el = document.activeElement;"
                    "  if (!el || el === document.body) el = document.querySelector('input:not([type=hidden]), textarea');"
                    "  if (!el) return {success: false, error: 'Element not found'};"
                    "  if (typeof el.scrollIntoView === 'function') el.scrollIntoView({block: 'center', inline: 'center'});"
                    "  el.focus();"
                    "  if (clear) el.value = '';"
                    "  el.value = clear ? text : (el.value + text);"
                    "  el.dispatchEvent(new Event('input', {bubbles: true}));"
                    "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                    "  if (pressEnter) {"
                    "    el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));"
                    "    el.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));"
                    "    if (el.form) el.form.submit();"
                    "  }"
                    "  return {success: true, tag: el.tagName, id: el.id, name: el.name, value: el.value};"
                    "})()"
                )
                eval_res = self.controller.cdp.evaluate(js_script)
                if isinstance(eval_res, dict) and eval_res.get("success"):
                    obs = self.controller.observe()
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Typed '{text_str}' into {eval_res.get('tag', 'element')} via CDP.",
                        data={
                            "text": text_str,
                            "element_id": element_id,
                            "selector": selector,
                            "press_enter": press_enter,
                            "clear_existing": clear_existing,
                            "method": "cdp",
                            "element_info": eval_res,
                            "observation": obs.to_dict(),
                        },
                    )
            except Exception as cdp_err:
                logger.debug("CDP typing failed, falling back to desktop input: %s", cdp_err)

        # 2. Desktop input fallback
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

        obs = self.controller.observe()
        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Typed '{text_str}' into browser window.",
            data={
                "text": text_str,
                "element_id": element_id,
                "selector": selector,
                "press_enter": press_enter,
                "clear_existing": clear_existing,
                "method": "desktop_input",
                "observation": obs.to_dict(),
            },
        )
