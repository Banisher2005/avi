"""Comprehensive unit tests for AVI Phase 5 - Real Browser Computer Use."""

from unittest.mock import MagicMock, patch
import pytest

from avi.agent.executor import AgentExecutor
from avi.agent.models import Plan, PlanStep, StepStatus
from avi.agent.planner import AgentPlanner
from avi.browser.controller import BrowserController
from avi.browser.models import (
    BrowserObservation,
    BrowserState,
    InteractiveElement,
    TabInfo,
)
from avi.capabilities.browser.click import BrowserClickCapability
from avi.capabilities.browser.keyboard import BrowserPressKeyCapability
from avi.capabilities.browser.input import BrowserTypeCapability
from avi.capabilities.browser.navigate import BrowserNavigateCapability
from avi.capabilities.browser.observe import BrowserObserveCapability
from avi.capabilities.browser.scroll import BrowserScrollCapability
from avi.capabilities.browser.tabs import BrowserTabsCapability
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


class TestInteractiveElementAndObservation:
    def test_interactive_element_properties_and_serialization(self):
        el = InteractiveElement(
            element_id=1,
            tag="button",
            element_type="submit",
            role="button",
            text="Search Kaggle",
            selector="button#search",
            is_visible=True,
        )
        d = el.to_dict()
        assert d["element_id"] == 1
        assert d["tag"] == "button"
        assert d["text"] == "Search Kaggle"
        assert d["selector"] == "button#search"
        assert "[1]" in el.format_repr()
        assert "Search Kaggle" in el.format_repr()

    def test_tab_info_serialization(self):
        tab = TabInfo(tab_id="tab_1", title="Kaggle: Your Machine Learning Community", url="https://kaggle.com", is_active=True)
        td = tab.to_dict()
        assert td["tab_id"] == "tab_1"
        assert td["is_active"] is True
        assert td["url"] == "https://kaggle.com"

    def test_browser_observation_get_and_find_elements(self):
        btn = InteractiveElement(element_id=1, tag="button", text="Submit Form", selector="button.submit")
        inp = InteractiveElement(element_id=2, tag="input", element_type="search", text="Query", placeholder="Search here", selector="input#q")
        link = InteractiveElement(element_id=3, tag="a", text="Documentation", href="https://example.com/docs")

        obs = BrowserObservation(
            url="https://example.com",
            title="Example Site",
            status="ready",
            elements=[btn, inp, link],
            tabs=[TabInfo(tab_id="t1", title="Example Site", url="https://example.com", is_active=True)],
        )

        assert obs.get_element(1) == btn
        assert obs.get_element("2") == inp
        assert obs.get_element("#3") == link
        assert obs.get_element(99) is None

        found_by_text = obs.find_elements(text="documentation")
        assert len(found_by_text) == 1
        assert found_by_text[0] == link

        found_by_tag = obs.find_elements(tag="button")
        assert len(found_by_tag) == 1
        assert found_by_tag[0] == btn

        summary = obs.formatted_summary()
        assert "Example Site" in summary
        assert "[1]" in summary
        assert "[2]" in summary


class TestBrowserControllerComputerUse:
    def test_observe_with_cdp_elements_extraction(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [
            {"id": "tab_1", "type": "page", "title": "GitHub", "url": "https://github.com"}
        ]
        ctrl.cdp.get_active_tab.return_value = {
            "id": "tab_1",
            "title": "GitHub",
            "url": "https://github.com",
        }
        ctrl.cdp.evaluate.return_value = {
            "title": "GitHub",
            "url": "https://github.com",
            "readyState": "complete",
            "loading": False,
            "viewport": {"scrollY": 0, "scrollX": 0, "innerHeight": 900},
            "visibleText": "The AI powered developer platform.",
            "elements": [
                {
                    "element_id": 1,
                    "tag": "input",
                    "element_type": "search",
                    "role": "searchbox",
                    "text": "Search or jump to...",
                    "placeholder": "Search or jump to...",
                    "name": "q",
                    "selector": "[data-avi-id=\"1\"]",
                },
                {
                    "element_id": 2,
                    "tag": "a",
                    "role": "link",
                    "text": "Sign in",
                    "href": "https://github.com/login",
                    "selector": "[data-avi-id=\"2\"]",
                },
            ],
        }

        obs = ctrl.observe()
        assert obs.cdp_connected is True
        assert obs.url == "https://github.com"
        assert obs.title == "GitHub"
        assert len(obs.elements) == 2
        assert obs.elements[0].element_id == 1
        assert obs.elements[0].tag == "input"
        assert obs.get_element(2).text == "Sign in"
        assert ctrl.get_last_observation() == obs

    def test_resolve_element_with_stale_recovery(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [{"id": "t1", "title": "Test", "url": "https://test.com"}]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Test", "url": "https://test.com"}

        # First observation has no element 5
        ctrl.cdp.evaluate.side_effect = [
            {"title": "Initial", "url": "https://test.com", "elements": [{"element_id": 1, "tag": "button", "text": "Old"}]},
            {"title": "Updated", "url": "https://test.com", "elements": [{"element_id": 5, "tag": "button", "text": "New Button"}]},
        ]

        ctrl.observe()
        assert ctrl.get_last_observation().get_element(5) is None

        # resolve_element should 1-shot re-observe and find element 5
        el, sel = ctrl.resolve_element(element_id=5)
        assert el is not None
        assert el.element_id == 5
        assert el.text == "New Button"


class TestBrowserNavigateComputerUse:
    def test_navigate_bounded_wait_and_observation(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.navigate.return_value = True
        ctrl.cdp.list_tabs.return_value = [{"id": "t1", "title": "Kaggle", "url": "https://kaggle.com"}]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Kaggle", "url": "https://kaggle.com"}
        ctrl.cdp.evaluate.side_effect = [
            "complete",  # readyState poll
            {
                "title": "Kaggle: Datasets & Code",
                "url": "https://kaggle.com",
                "elements": [{"element_id": 1, "tag": "input", "text": "Search"}],
            },
        ]

        cap = BrowserNavigateCapability(controller=ctrl)
        res = cap.execute(url="https://kaggle.com", wait_seconds=0.2)

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert "observation" in res.data
        assert res.data["observation"]["url"] == "https://kaggle.com"
        assert res.data["verified"] is True


class TestBrowserClickComputerUse:
    def test_click_by_element_id(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [{"id": "t1", "title": "Store", "url": "https://store.com"}]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Store", "url": "https://store.com"}
        ctrl.cdp.evaluate.side_effect = [
            {"title": "Store", "url": "https://store.com", "elements": [{"element_id": 3, "tag": "button", "text": "Add to Cart"}]},
            {"success": True, "tag": "BUTTON", "text": "Add to Cart", "href": None},  # click evaluation
            {"title": "Store - Cart Updated", "url": "https://store.com/cart", "elements": []},  # post-observation
        ]

        ctrl.observe()

        cap = BrowserClickCapability(controller=ctrl)
        res = cap.execute(element_id=3)

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["element_id"] == 3
        assert "observation" in res.data
        assert res.data["observation"]["url"] == "https://store.com/cart"

    def test_high_risk_click_requires_confirmation_without_flag(self):
        ctrl = BrowserController()
        ctrl._last_observation = BrowserObservation(
            url="https://shop.com/checkout",
            elements=[InteractiveElement(element_id=7, tag="button", text="Buy Now $49.99")],
        )

        cap = BrowserClickCapability(controller=ctrl)
        res = cap.execute(element_id=7, confirmed=False)

        assert res.success is False
        assert res.status == ExecutionStatus.CONFIRMATION_REQUIRED
        assert "Confirmation required" in res.message
        assert res.data["confirmation_required"] is True

    def test_high_risk_click_proceeds_when_confirmed(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [{"id": "t1", "title": "Shop", "url": "https://shop.com"}]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Shop", "url": "https://shop.com"}
        ctrl._last_observation = BrowserObservation(
            url="https://shop.com/checkout",
            elements=[InteractiveElement(element_id=7, tag="button", text="Buy Now $49.99")],
        )
        ctrl.cdp.evaluate.side_effect = [
            {"success": True, "tag": "BUTTON", "text": "Buy Now $49.99"},  # click
            {"title": "Order Placed", "url": "https://shop.com/success", "elements": []},  # observe
        ]

        cap = BrowserClickCapability(controller=ctrl)
        res = cap.execute(element_id=7, confirmed=True)

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert "observation" in res.data


class TestBrowserTypeComputerUse:
    def test_type_by_element_id(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [{"id": "t1", "title": "Search", "url": "https://search.com"}]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Search", "url": "https://search.com"}
        ctrl._last_observation = BrowserObservation(
            url="https://search.com",
            elements=[InteractiveElement(element_id=2, tag="input", element_type="search", text="", selector="input#search")],
        )
        ctrl.cdp.evaluate.side_effect = [
            {"success": True, "tag": "INPUT", "id": "search", "name": "q", "value": "deep learning"},
            {"title": "Search Results", "url": "https://search.com?q=deep+learning", "elements": []},
        ]

        cap = BrowserTypeCapability(controller=ctrl)
        res = cap.execute(element_id=2, text="deep learning", press_enter=True)

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["element_id"] == 2
        assert res.data["text"] == "deep learning"
        assert "observation" in res.data


class TestBrowserPressKeyAndScroll:
    def test_browser_press_key_capability(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [{"id": "t1", "title": "Page", "url": "https://page.com"}]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Page", "url": "https://page.com"}
        ctrl.cdp.evaluate.side_effect = [
            {"success": True, "key": "Enter"},
            {"title": "Page", "url": "https://page.com", "elements": []},
        ]

        cap = BrowserPressKeyCapability(controller=ctrl)
        res = cap.execute(key="Enter")

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["key"] == "Enter"
        assert "observation" in res.data

    def test_browser_scroll_capability(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [{"id": "t1", "title": "Feed", "url": "https://feed.com"}]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Feed", "url": "https://feed.com"}
        ctrl.cdp.evaluate.side_effect = [
            {"success": True, "scrollYBefore": 0, "scrollYAfter": 600, "direction": "down"},
            {"title": "Feed", "url": "https://feed.com", "elements": []},
        ]

        cap = BrowserScrollCapability(controller=ctrl)
        res = cap.execute(direction="down")

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["direction"] == "down"
        assert "observation" in res.data


class TestBrowserTabsCapability:
    def test_tabs_list_and_new_and_close(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.list_tabs.return_value = [
            {"id": "t1", "type": "page", "title": "Tab 1", "url": "https://site1.com"}
        ]
        ctrl.cdp.get_active_tab.return_value = {"id": "t1", "title": "Tab 1", "url": "https://site1.com"}
        ctrl.cdp.evaluate.return_value = {"title": "Tab 1", "url": "https://site1.com", "elements": []}
        ctrl.cdp.new_tab.return_value = {"id": "t2", "title": "Tab 2", "url": "https://site2.com"}
        ctrl.cdp.close_tab.return_value = True

        cap = BrowserTabsCapability(controller=ctrl)

        # 1. List tabs
        res_list = cap.execute(action="list")
        assert res_list.success is True
        assert len(res_list.data["tabs"]) == 1

        # 2. New tab
        res_new = cap.execute(action="new", url="https://site2.com")
        assert res_new.success is True
        assert res_new.data["new_tab"]["tab_id"] == "t2"

        # 3. Close tab
        res_close = cap.execute(action="close", tab_id="t1")
        assert res_close.success is True
        assert res_close.data["tab_id"] == "t1"


class TestAgentPlannerBrowserComputerUse:
    def test_multi_step_observe_type_click_workflow(self):
        planner = AgentPlanner(max_steps=6)
        plan = planner.create_plan("open kaggle, search for titanic, and open the first result")
        assert plan is not None
        assert len(plan.steps) == 6

        # Step sequence must follow Observe -> Act -> Observe -> Act -> Observe
        step_caps = [s.capability_name for s in plan.steps]
        assert step_caps == [
            "browser.navigate",
            "browser.observe",
            "browser.type",
            "browser.observe",
            "browser.click",
            "browser.observe",
        ]
        assert "kaggle" in plan.steps[0].arguments["url"].lower()
        assert plan.steps[2].arguments["text"] == "titanic"
        assert plan.steps[2].arguments["press_enter"] is True

        # When max_steps is 5
        planner5 = AgentPlanner(max_steps=5)
        plan5 = planner5.create_plan("open kaggle, search for titanic, and open the first result")
        assert plan5 is not None
        assert len(plan5.steps) == 5

    def test_atomic_browser_command_plans(self):
        planner = AgentPlanner()

        p_click = planner.create_plan("in browser, click element 4")
        assert p_click is not None
        assert p_click.steps[0].capability_name == "browser.click"
        assert p_click.steps[0].arguments["element_id"] == 4
        assert p_click.steps[1].capability_name == "browser.observe"

        p_type = planner.create_plan("type 'pytorch' into element 2 in browser")
        assert p_type is not None
        assert p_type.steps[0].capability_name == "browser.type"
        assert p_type.steps[0].arguments["text"] == "pytorch"
        assert p_type.steps[0].arguments["element_id"] == 2

        p_key = planner.create_plan("press Enter key in browser")
        assert p_key is not None
        assert p_key.steps[0].capability_name == "browser.press_key"
        assert p_key.steps[0].arguments["key"] == "Enter"

        p_scroll = planner.create_plan("scroll down in browser")
        assert p_scroll is not None
        assert p_scroll.steps[0].capability_name == "browser.scroll"
        assert p_scroll.steps[0].arguments["direction"] == "down"


class TestAgentExecutorBrowserVerification:
    def test_executor_verifies_all_browser_steps(self):
        reg = create_default_capability_registry()
        executor = AgentExecutor(registry=reg)

        step_nav = PlanStep(step_id=1, capability_name="browser.navigate", arguments={"url": "https://example.com"})
        res_nav = MagicMock(success=True, data={"url": "https://example.com", "observation": {}})
        assert executor._verify_step(step_nav, res_nav) is True

        step_obs = PlanStep(step_id=2, capability_name="browser.observe", arguments={})
        res_obs = MagicMock(success=True, data={"status": "ready", "observation": {}})
        assert executor._verify_step(step_obs, res_obs) is True

        step_type = PlanStep(step_id=3, capability_name="browser.type", arguments={"text": "hi"})
        res_type = MagicMock(success=True, data={"text": "hi", "observation": {}})
        assert executor._verify_step(step_type, res_type) is True

        step_click = PlanStep(step_id=4, capability_name="browser.click", arguments={"element_id": 1})
        res_click = MagicMock(success=True, data={"element_id": 1, "observation": {}})
        assert executor._verify_step(step_click, res_click) is True

        step_key = PlanStep(step_id=5, capability_name="browser.press_key", arguments={"key": "Enter"})
        res_key = MagicMock(success=True, data={"key": "Enter"})
        assert executor._verify_step(step_key, res_key) is True

        step_tabs = PlanStep(step_id=6, capability_name="browser.tabs", arguments={"action": "list"})
        res_tabs = MagicMock(success=True, data={"action": "list", "tabs": []})
        assert executor._verify_step(step_tabs, res_tabs) is True
