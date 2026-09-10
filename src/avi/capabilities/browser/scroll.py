"""Browser viewport scrolling capability."""

from __future__ import annotations

import json
import logging
from typing import Any

from avi.browser.controller import BrowserController
from avi.capabilities.desktop.input import PressKeyCapability
from avi.capabilities.desktop.window import WindowFocusCapability
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger(__name__)

VALID_DIRECTIONS = {"down", "up", "top", "bottom"}


class BrowserScrollCapability(BaseCapability):
    """Scroll the browser viewport in a specified direction."""

    name = "browser.scroll"
    description = (
        "Scroll the browser viewport in a specified direction (down, up, top, bottom). "
        "Supports full page, half page, or pixel amounts."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["down", "up", "top", "bottom"],
                "description": "The scroll direction: 'down', 'up', 'top', or 'bottom'.",
                "default": "down",
            },
            "amount": {
                "type": ["string", "integer"],
                "description": "Scroll distance: 'page', 'half_page', or pixel integer (e.g. 500).",
                "default": "page",
            },
        },
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(
        self,
        controller: BrowserController | None = None,
        press_cap: PressKeyCapability | None = None,
        focus_cap: WindowFocusCapability | None = None,
    ) -> None:
        self.controller = controller or BrowserController()
        self.press_cap = press_cap or PressKeyCapability()
        self.focus_cap = focus_cap or WindowFocusCapability()
        self.tags = ("browser", "web", "scroll", "viewport", "page")
        self.aliases = [
            "scroll_page",
            "browser.scroll_page",
            "web.scroll",
            "scroll_down",
            "scroll_up",
        ]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute scrolling in the browser."""
        direction = str(kwargs.get("direction", "down")).strip().lower()
        amount = kwargs.get("amount", "page")

        if direction not in VALID_DIRECTIONS:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Invalid scroll direction '{direction}'. Must be one of: {sorted(VALID_DIRECTIONS)}",
                message=f"Unknown scroll direction: {direction}",
            )

        cdp_used = False

        # 1. Try CDP smooth scroll
        if self.controller.cdp.is_available():
            try:
                js_scroll = (
                    "(() => {"
                    f"  const dir = {json.dumps(direction)};"
                    f"  const amt = {json.dumps(amount)};"
                    "  const scrollYBefore = window.scrollY;"
                    "  if (dir === 'top') {"
                    "    window.scrollTo({top: 0, behavior: 'smooth'});"
                    "  } else if (dir === 'bottom') {"
                    "    window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});"
                    "  } else {"
                    "    let dy = 600;"
                    "    if (typeof amt === 'number') {"
                    "      dy = amt;"
                    "    } else if (amt === 'half_page') {"
                    "      dy = window.innerHeight * 0.5;"
                    "    } else {"
                    "      dy = window.innerHeight * 0.85;"
                    "    }"
                    "    if (dir === 'up') dy = -dy;"
                    "    window.scrollBy({top: dy, behavior: 'smooth'});"
                    "  }"
                    "  return {"
                    "    success: true,"
                    "    scrollYBefore: scrollYBefore,"
                    "    scrollYAfter: window.scrollY,"
                    "    direction: dir"
                    "  };"
                    "})()"
                )
                res = self.controller.cdp.evaluate(js_scroll)
                if isinstance(res, dict) and res.get("success"):
                    cdp_used = True
                    obs = self.controller.observe()
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Scrolled browser {direction} ({amount}) via CDP.",
                        data={
                            "direction": direction,
                            "amount": amount,
                            "method": "cdp",
                            "metrics": res,
                            "observation": obs.to_dict(),
                        },
                    )
            except Exception as cdp_err:
                logger.debug("CDP scroll failed: %s", cdp_err)

        # 2. Desktop keypress fallback
        state = self.controller.observe()
        if state.is_running and state.browser_name:
            self.focus_cap.execute(app=state.browser_name)

        key_map = {
            "down": "Page_Down",
            "up": "Page_Up",
            "top": "Home",
            "bottom": "End",
        }
        key_to_press = key_map.get(direction, "Page_Down")
        press_res = self.press_cap.execute(key=key_to_press)

        obs = self.controller.observe()
        return CapabilityResult(
            success=press_res.success,
            status=ExecutionStatus.SUCCESS if press_res.success else ExecutionStatus.FAILED,
            message=f"Scrolled browser {direction} using keyboard shortcut ({key_to_press}).",
            data={
                "direction": direction,
                "amount": amount,
                "key": key_to_press,
                "method": "keyboard_fallback",
                "observation": obs.to_dict(),
            },
        )
