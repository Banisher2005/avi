"""Unit tests for Browser Navigation Capability."""

from unittest.mock import MagicMock, patch
import pytest

from avi.browser.controller import BrowserController
from avi.capabilities.browser.navigate import (
    BrowserNavigateCapability,
    sanitize_and_validate_url,
)
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


class TestUrlSanitization:
    def test_auto_prepend_https(self):
        url = sanitize_and_validate_url("github.com/torvalds/linux")
        assert url == "https://github.com/torvalds/linux"

    def test_preserve_http_and_https(self):
        assert sanitize_and_validate_url("https://news.ycombinator.com") == "https://news.ycombinator.com"
        assert sanitize_and_validate_url("http://example.com/test") == "http://example.com/test"

    def test_reject_dangerous_schemes(self):
        with pytest.raises(ValueError, match="disallowed for safety"):
            sanitize_and_validate_url("javascript:alert(1)")

        with pytest.raises(ValueError, match="disallowed for safety"):
            sanitize_and_validate_url("data:text/html,<html></html>")

        with pytest.raises(ValueError, match="disallowed for safety"):
            sanitize_and_validate_url("file:///etc/passwd")

    def test_reject_empty_or_invalid(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            sanitize_and_validate_url("")

        with pytest.raises(ValueError, match="must include a valid host"):
            sanitize_and_validate_url("https://")


class TestBrowserNavigateCapability:
    def test_missing_url(self):
        cap = BrowserNavigateCapability()
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "required" in res.error.lower()

    def test_invalid_scheme_rejected(self):
        cap = BrowserNavigateCapability()
        res = cap.execute(url="javascript:eval('boom')")
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "disallowed" in res.error

    @patch("avi.capabilities.browser.navigate.OpenUrlAction")
    def test_navigation_via_system_browser_fallback(self, mock_action_cls):
        mock_action = MagicMock()
        mock_action.execute.return_value = MagicMock(success=True, message="Opened in browser")
        mock_action_cls.return_value = mock_action

        ctrl = BrowserController()
        cap = BrowserNavigateCapability(controller=ctrl)

        res = cap.execute(url="python.org")
        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["url"] == "https://python.org"
        assert res.data["method"] == "system_browser"
        assert ctrl._last_navigated_url == "https://python.org"

    def test_navigation_via_cdp(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.navigate.return_value = True

        cap = BrowserNavigateCapability(controller=ctrl)
        res = cap.execute(url="https://docs.python.org/3/")

        assert res.success is True
        assert res.data["method"] == "cdp"
        assert res.data["cdp_used"] is True
        ctrl.cdp.navigate.assert_called_once_with("https://docs.python.org/3/")

    def test_navigation_new_tab_via_cdp(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.new_tab.return_value = {"id": "new_tab_123"}

        cap = BrowserNavigateCapability(controller=ctrl)
        res = cap.execute(url="https://news.ycombinator.com", new_tab=True)

        assert res.success is True
        assert res.data["method"] == "cdp"
        ctrl.cdp.new_tab.assert_called_once_with("https://news.ycombinator.com")

    def test_registry_integration(self):
        reg = create_default_capability_registry()
        cap = reg.get("browser.navigate")
        assert cap is not None
        assert reg.get("navigate") is cap
        assert reg.get("goto_url") is cap
