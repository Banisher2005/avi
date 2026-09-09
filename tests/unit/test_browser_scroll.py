"""Unit tests for Browser Viewport Scrolling Capability."""

from unittest.mock import MagicMock

from avi.browser.controller import BrowserController
from avi.capabilities.browser.scroll import BrowserScrollCapability
from avi.capabilities.desktop.input import PressKeyCapability
from avi.capabilities.desktop.window import WindowFocusCapability
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


class TestBrowserScrollCapability:
    def test_invalid_direction_rejected(self):
        cap = BrowserScrollCapability()
        res = cap.execute(direction="diagonal")
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "direction" in res.error.lower()

    def test_scroll_down_via_cdp(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.evaluate.return_value = {
            "success": True,
            "scrollYBefore": 0,
            "scrollYAfter": 600,
            "direction": "down",
        }

        cap = BrowserScrollCapability(controller=ctrl)
        res = cap.execute(direction="down", amount="page")

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["method"] == "cdp"
        assert res.data["direction"] == "down"

    def test_scroll_top_via_cdp(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.evaluate.return_value = {
            "success": True,
            "scrollYBefore": 1500,
            "scrollYAfter": 0,
            "direction": "top",
        }

        cap = BrowserScrollCapability(controller=ctrl)
        res = cap.execute(direction="top")

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["direction"] == "top"

    def test_scroll_keyboard_fallback(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = False

        mock_press = MagicMock(spec=PressKeyCapability)
        mock_press.execute.return_value = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, message="Key pressed"
        )
        mock_focus = MagicMock(spec=WindowFocusCapability)
        mock_focus.execute.return_value = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, message="Window focused"
        )

        cap = BrowserScrollCapability(
            controller=ctrl,
            press_cap=mock_press,
            focus_cap=mock_focus,
        )

        # Down -> Page_Down
        res_down = cap.execute(direction="down")
        assert res_down.success is True
        mock_press.execute.assert_called_with(key="Page_Down")

        # Up -> Page_Up
        res_up = cap.execute(direction="up")
        assert res_up.success is True
        mock_press.execute.assert_called_with(key="Page_Up")

        # Top -> Home
        res_top = cap.execute(direction="top")
        assert res_top.success is True
        mock_press.execute.assert_called_with(key="Home")

        # Bottom -> End
        res_bottom = cap.execute(direction="bottom")
        assert res_bottom.success is True
        mock_press.execute.assert_called_with(key="End")

    def test_registry_integration(self):
        reg = create_default_capability_registry()
        cap = reg.get("browser.scroll")
        assert cap is not None
        assert reg.get("scroll_page") is cap
        assert reg.get("scroll_down") is cap
        assert reg.get("web.scroll") is cap
