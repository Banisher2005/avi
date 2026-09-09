"""Unit tests for Browser State Observation and CDP Client."""

from unittest.mock import MagicMock, patch

from avi.browser.cdp import CdpClient
from avi.browser.controller import BrowserController
from avi.browser.models import BrowserState
from avi.capabilities.browser.observe import BrowserObserveCapability
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


class TestBrowserStateModel:
    def test_default_browser_state(self):
        state = BrowserState()
        assert state.url is None
        assert state.title is None
        assert state.is_running is False
        assert state.status == "idle"
        assert state.tab_count == 0

        d = state.to_dict()
        assert isinstance(d, dict)
        assert d["status"] == "idle"
        assert d["is_running"] is False

    def test_browser_state_custom_values(self):
        state = BrowserState(
            url="https://example.com",
            title="Example Domain",
            browser_name="google-chrome",
            is_running=True,
            status="ready",
            tab_count=3,
        )
        assert state.url == "https://example.com"
        assert state.to_dict()["browser_name"] == "google-chrome"


class TestCdpClient:
    def test_cdp_unavailable_when_port_closed(self):
        client = CdpClient(port=64999, timeout=0.1)
        assert client.is_available() is False
        assert client.list_tabs() == []
        assert client.get_active_tab() is None

    @patch("urllib.request.urlopen")
    def test_cdp_list_tabs_mocked(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b'[{"id": "tab1", "type": "page", "title": "Test Page", "url": "https://test.com"}]'
        )
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        client = CdpClient(port=9222)
        tabs = client.list_tabs()
        assert len(tabs) == 1
        assert tabs[0]["id"] == "tab1"

        active = client.get_active_tab()
        assert active is not None
        assert active["url"] == "https://test.com"


class TestBrowserController:
    def test_detect_running_browsers(self):
        ctrl = BrowserController()
        # Even if none found, returns a list without error
        running = ctrl.detect_running_browsers()
        assert isinstance(running, list)

    def test_observe_with_window_list(self):
        ctrl = BrowserController()
        mock_windows = [
            {"id": "1", "title": "GitHub - Google Chrome", "class": "google-chrome"},
        ]
        state = ctrl.observe(window_list=mock_windows)
        assert state.is_active_window is True
        assert state.status == "ready"
        assert "GitHub" in (state.title or "")

    def test_record_navigation(self):
        ctrl = BrowserController()
        ctrl.record_navigation("https://news.ycombinator.com", "Hacker News")
        state = ctrl.observe(window_list=[])
        assert state.url == "https://news.ycombinator.com"


class TestBrowserObserveCapability:
    def test_capability_metadata(self):
        cap = BrowserObserveCapability()
        assert cap.name == "browser.observe"
        assert "browser.state" in cap.aliases
        assert not cap.requires_confirmation

    def test_capability_execution(self):
        mock_ctrl = MagicMock(spec=BrowserController)
        mock_ctrl.observe.return_value = BrowserState(
            url="https://python.org",
            title="Python Language",
            browser_name="firefox",
            is_running=True,
            status="ready",
        )
        cap = BrowserObserveCapability(controller=mock_ctrl)
        result = cap.execute()
        assert result.success is True
        assert result.status == ExecutionStatus.SUCCESS
        assert result.data["url"] == "https://python.org"
        assert "Python Language" in result.message

    def test_capability_registry_integration(self):
        reg = create_default_capability_registry()
        cap = reg.get("browser.observe")
        assert cap is not None
        assert reg.get("browser.state") is cap
