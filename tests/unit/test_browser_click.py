"""Unit tests for Browser Semantic Click Capability."""

from unittest.mock import MagicMock

from avi.browser.controller import BrowserController
from avi.capabilities.browser.click import BrowserClickCapability
from avi.capabilities.browser.navigate import BrowserNavigateCapability
from avi.capabilities.desktop.window import WindowFocusCapability
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


class TestBrowserClickCapability:
    def test_missing_selector_and_text(self):
        cap = BrowserClickCapability()
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "required" in res.error.lower()

    def test_click_by_selector_via_cdp(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.evaluate.return_value = {
            "success": True,
            "tag": "BUTTON",
            "text": "Submit Search",
            "href": None,
        }

        cap = BrowserClickCapability(controller=ctrl)
        res = cap.execute(selector="#search-btn")

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["method"] == "cdp"
        assert "BUTTON" in res.message
        assert res.data["selector"] == "#search-btn"

    def test_click_by_text_with_link_href_records_navigation(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.evaluate.return_value = {
            "success": True,
            "tag": "A",
            "text": "Documentation",
            "href": "https://example.com/docs",
        }

        cap = BrowserClickCapability(controller=ctrl)
        res = cap.execute(text="Documentation")

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert ctrl._last_navigated_url == "https://example.com/docs"

    def test_click_element_not_found_cdp(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.evaluate.return_value = {"success": False, "error": "Element not found"}

        cap = BrowserClickCapability(controller=ctrl)
        res = cap.execute(selector="#non-existent")

        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "not found" in res.error.lower()

    def test_click_url_text_delegates_to_navigate(self):
        mock_nav = MagicMock(spec=BrowserNavigateCapability)
        mock_nav.execute.return_value = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, message="Navigated to https://avi.dev"
        )
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = False

        cap = BrowserClickCapability(controller=ctrl, nav_cap=mock_nav)
        res = cap.execute(text="https://avi.dev")

        assert res.success is True
        mock_nav.execute.assert_called_once_with(url="https://avi.dev")

    def test_registry_integration(self):
        reg = create_default_capability_registry()
        cap = reg.get("browser.click")
        assert cap is not None
        assert reg.get("click_element") is cap
        assert reg.get("browser.click_element") is cap
