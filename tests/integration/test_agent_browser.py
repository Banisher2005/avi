"""Integration tests for Browser Computer Use in Agentic Architecture."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from avi.agent.events import EventDispatcher
from avi.agent.executor import AgentExecutor
from avi.agent.models import Plan, PlanStep, StepStatus
from avi.agent.tool_selection import ToolSelector
from avi.browser.controller import BrowserController
from avi.browser.downloads import DownloadsWatcher
from avi.browser.models import BrowserState
from avi.capabilities.browser.click import BrowserClickCapability
from avi.capabilities.browser.download import BrowserDownloadCapability
from avi.capabilities.browser.extract import BrowserExtractCapability
from avi.capabilities.browser.input import BrowserTypeCapability
from avi.capabilities.browser.navigate import BrowserNavigateCapability
from avi.capabilities.browser.observe import BrowserObserveCapability
from avi.capabilities.browser.scroll import BrowserScrollCapability
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry, create_default_capability_registry
from avi.safety.engine import SafetyEngine


class TestToolSelectorBrowserRanking:
    @pytest.fixture
    def registry(self):
        return create_default_capability_registry()

    def test_selector_prioritizes_browser_navigate(self, registry):
        selector = ToolSelector(registry)
        selected = selector.select_capabilities("navigate to https://github.com in browser")
        names = [s["name"] for s in selected]
        assert "browser.navigate" in names

    def test_selector_prioritizes_browser_extract(self, registry):
        selector = ToolSelector(registry)
        selected = selector.select_capabilities("read the webpage content and extract text")
        names = [s["name"] for s in selected]
        assert "browser.extract" in names

    def test_selector_prioritizes_browser_scroll(self, registry):
        selector = ToolSelector(registry)
        selected = selector.select_capabilities("scroll down the page in the browser")
        names = [s["name"] for s in selected]
        assert "browser.scroll" in names

    def test_selector_prioritizes_browser_download(self, registry):
        selector = ToolSelector(registry)
        selected = selector.select_capabilities("check my downloaded files from the browser")
        names = [s["name"] for s in selected]
        assert "browser.download" in names


class TestAgentBrowserPipeline:
    @pytest.fixture
    def mock_controller(self):
        ctrl = MagicMock(spec=BrowserController)
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.navigate.return_value = True
        ctrl.cdp.evaluate.return_value = {
            "success": True,
            "url": "https://news.ycombinator.com",
            "title": "Hacker News",
            "text": "Top tech headlines today. 1. Rust 2026 Edition 2. AI Agents",
            "headings": ["Hacker News"],
            "links": [{"text": "Comments", "href": "https://news.ycombinator.com/item?id=1"}],
        }
        ctrl.observe.return_value = BrowserState(
            url="https://news.ycombinator.com",
            title="Hacker News",
            browser_name="google-chrome",
            is_running=True,
            status="ready",
        )
        ctrl._last_navigated_url = "https://news.ycombinator.com"
        return ctrl

    @pytest.fixture
    def custom_registry(self, mock_controller):
        reg = CapabilityRegistry()
        reg.register(BrowserNavigateCapability(controller=mock_controller))
        reg.register(BrowserObserveCapability(controller=mock_controller))
        reg.register(BrowserScrollCapability(controller=mock_controller))
        reg.register(BrowserExtractCapability(controller=mock_controller))
        reg.register(BrowserTypeCapability(controller=mock_controller))
        reg.register(BrowserClickCapability(controller=mock_controller))
        return reg

    def test_four_step_browser_workflow(self, custom_registry):
        """Test multi-step browser pipeline: Navigate -> Observe -> Scroll -> Extract."""
        events = EventDispatcher()
        safety = SafetyEngine()
        executor = AgentExecutor(
            registry=custom_registry,
            safety_engine=safety,
            event_dispatcher=events,
            step_timeout=5.0,
        )

        plan = Plan(
            user_goal="Navigate to Hacker News, scroll down, and extract the page headlines",
            steps=[
                PlanStep(
                    step_id=1,
                    description="Navigate to Hacker News",
                    capability_name="browser.navigate",
                    arguments={"url": "https://news.ycombinator.com"},
                ),
                PlanStep(
                    step_id=2,
                    description="Observe current browser state",
                    capability_name="browser.observe",
                    arguments={},
                ),
                PlanStep(
                    step_id=3,
                    description="Scroll down the webpage",
                    capability_name="browser.scroll",
                    arguments={"direction": "down", "amount": "page"},
                ),
                PlanStep(
                    step_id=4,
                    description="Extract headlines and text from the webpage",
                    capability_name="browser.extract",
                    arguments={"url": "https://news.ycombinator.com", "max_length": 500},
                ),
            ],
        )

        result = executor.execute_plan(plan)

        assert result.success is True
        assert result.status == ExecutionStatus.SUCCESS
        assert len(result.completed_steps) == 4

        # Step 1: Navigate
        step1 = result.completed_steps[0]
        assert step1.status == StepStatus.SUCCESS
        assert step1.verified is True
        assert step1.result.data["url"] == "https://news.ycombinator.com"

        # Step 2: Observe
        step2 = result.completed_steps[1]
        assert step2.status == StepStatus.SUCCESS
        assert step2.verified is True
        assert step2.result.data["browser_name"] == "google-chrome"
        assert step2.result.data["status"] == "ready"

        # Step 3: Scroll
        step3 = result.completed_steps[2]
        assert step3.status == StepStatus.SUCCESS
        assert step3.verified is True
        assert step3.result.data["direction"] == "down"

        # Step 4: Extract
        step4 = result.completed_steps[3]
        assert step4.status == StepStatus.SUCCESS
        assert step4.verified is True
        assert "Hacker News" in step4.result.data["title"]
        assert "AI Agents" in step4.result.data["text"]

    def test_browser_search_and_click_pipeline(self, custom_registry, mock_controller):
        """Test typing a query into a search field and clicking the search button."""
        events = EventDispatcher()
        safety = SafetyEngine()
        executor = AgentExecutor(
            registry=custom_registry,
            safety_engine=safety,
            event_dispatcher=events,
            step_timeout=5.0,
        )

        plan = Plan(
            user_goal="Search for python documentation and click submit",
            steps=[
                PlanStep(
                    step_id=1,
                    description="Type search query",
                    capability_name="browser.type",
                    arguments={"text": "python asyncio", "selector": "input[name='q']"},
                ),
                PlanStep(
                    step_id=2,
                    description="Click submit button",
                    capability_name="browser.click",
                    arguments={"selector": "button#submit"},
                ),
            ],
        )

        result = executor.execute_plan(plan)

        assert result.success is True
        assert len(result.completed_steps) == 2
        assert result.completed_steps[0].verified is True
        assert result.completed_steps[1].verified is True

    def test_browser_download_detection_in_pipeline(self, tmp_path: Path):
        """Test triggering and verifying a download in the pipeline."""
        watcher = DownloadsWatcher(downloads_dir=tmp_path)
        cap_dl = BrowserDownloadCapability(watcher=watcher)

        reg = CapabilityRegistry()
        reg.register(cap_dl)

        # Create a file in the downloads folder
        dl_file = tmp_path / "dataset.zip"
        dl_file.write_bytes(b"DATASET_CONTENT_BYTES_1234567890")

        events = EventDispatcher()
        safety = SafetyEngine()
        executor = AgentExecutor(
            registry=reg,
            safety_engine=safety,
            event_dispatcher=events,
        )

        plan = Plan(
            user_goal="Verify completed download of dataset",
            steps=[
                PlanStep(
                    step_id=1,
                    description="Detect completed dataset download",
                    capability_name="browser.download",
                    arguments={"filename": "*.zip"},
                )
            ],
        )

        result = executor.execute_plan(plan)

        assert result.success is True
        step1 = result.completed_steps[0]
        assert step1.verified is True
        assert step1.result.data["count"] == 1
        assert step1.result.data["downloads"][0]["filename"] == "dataset.zip"

    def test_browser_invalid_url_fails_gracefully_without_hanging(self, custom_registry):
        """Test that attempting to navigate to an invalid or dangerous URL fails boundedly."""
        events = EventDispatcher()
        safety = SafetyEngine()
        executor = AgentExecutor(
            registry=custom_registry,
            safety_engine=safety,
            event_dispatcher=events,
            step_timeout=2.0,
        )

        plan = Plan(
            user_goal="Attempt invalid javascript URL",
            steps=[
                PlanStep(
                    step_id=1,
                    description="Navigate to dangerous URL",
                    capability_name="browser.navigate",
                    arguments={"url": "javascript:void(0)"},
                )
            ],
        )

        result = executor.execute_plan(plan)

        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert "disallowed" in (result.error or "").lower()
