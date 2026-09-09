"""Unit tests for Browser Text Input Capability."""

from unittest.mock import MagicMock

from avi.browser.controller import BrowserController
from avi.capabilities.browser.input import BrowserTypeCapability
from avi.capabilities.desktop.input import PressKeyCapability, TypeTextCapability
from avi.capabilities.desktop.window import WindowFocusCapability
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


class TestBrowserTypeCapability:
    def test_missing_text_parameter(self):
        cap = BrowserTypeCapability()
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "required" in res.error.lower()

    def test_type_via_cdp_with_selector(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.evaluate.return_value = {"success": True, "tag": "INPUT", "id": "search"}

        cap = BrowserTypeCapability(controller=ctrl)
        res = cap.execute(text="python asyncio tutorial", selector="input[name='q']", press_enter=True)

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["method"] == "cdp"
        assert res.data["text"] == "python asyncio tutorial"
        assert res.data["press_enter"] is True
        assert "INPUT" in res.message

    def test_type_via_desktop_input_fallback(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = False

        mock_type = MagicMock(spec=TypeTextCapability)
        mock_type.execute.return_value = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, message="Typed text"
        )
        mock_press = MagicMock(spec=PressKeyCapability)
        mock_press.execute.return_value = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, message="Pressed key"
        )
        mock_focus = MagicMock(spec=WindowFocusCapability)
        mock_focus.execute.return_value = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, message="Focused"
        )

        cap = BrowserTypeCapability(
            controller=ctrl,
            type_cap=mock_type,
            press_cap=mock_press,
            focus_cap=mock_focus,
        )

        res = cap.execute(text="open source AI", clear_existing=True, press_enter=True)

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["method"] == "desktop_input"
        mock_type.execute.assert_called_once_with(text="open source AI")
        # clear_existing calls ctrl+a and BackSpace, press_enter calls Return
        assert mock_press.execute.call_count >= 3

    def test_registry_integration(self):
        reg = create_default_capability_registry()
        cap = reg.get("browser.type")
        assert cap is not None
        assert reg.get("browser.input") is cap
        assert reg.get("type_into_browser") is cap
