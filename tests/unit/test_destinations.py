"""Unit tests for the smart destination-resolution layer and planning integration."""

from unittest.mock import patch

import pytest

from avi.actions.base import ActionResult
from avi.agent.planner import AgentPlanner
from avi.apps.destinations import DestinationResolver
from avi.apps.models import DestinationType
from avi.assistant.intents import AssistantIntentType, detect_assistant_intent
from avi.browser.models import BrowserState
from avi.config import Config
from avi.orchestrator.orchestrator import AssistantOrchestrator


class TestDestinationResolver:
    @pytest.fixture
    def resolver(self):
        return DestinationResolver()

    @pytest.mark.parametrize(
        ("query", "expected_type", "expected_url_contains"),
        [
            ("open kaggle", DestinationType.WEBSITE, "kaggle.com"),
            ("open github", DestinationType.WEBSITE, "github.com"),
            ("open chatgpt", DestinationType.WEBSITE, "chatgpt.com"),
            ("open youtube", DestinationType.WEBSITE, "youtube.com"),
            ("open google", DestinationType.WEBSITE, "google.com"),
            ("open reddit", DestinationType.WEBSITE, "reddit.com"),
            ("open wikipedia", DestinationType.WEBSITE, "wikipedia.org"),
            ("open kaggle.com", DestinationType.WEBSITE, "kaggle.com"),
            ("open https://kaggle.com", DestinationType.WEBSITE, "kaggle.com"),
            ("visit reddit", DestinationType.WEBSITE, "reddit.com"),
            ("go to github", DestinationType.WEBSITE, "github.com"),
        ],
    )
    def test_known_destinations_resolve_to_website(self, resolver, query, expected_type, expected_url_contains):
        res = resolver.resolve(query)
        assert res.destination_type == expected_type
        assert res.url is not None
        assert expected_url_contains in res.url

    def test_destination_browser_target(self, resolver):
        res = resolver.resolve("open github in chrome")
        assert res.destination_type == DestinationType.WEBSITE
        assert "github.com" in res.url
        assert res.browser == "chrome"

    def test_explicit_app_modifier(self, resolver):
        res = resolver.resolve("open the app Kaggle")
        assert res.destination_type == DestinationType.APPLICATION
        assert res.is_explicit_app is True

    @pytest.mark.parametrize(
        ("query", "expected_canonical"),
        [
            ("open youtub", "YouTube"),
            ("open githb", "GitHub"),
            ("open chatgptt", "ChatGPT"),
        ],
    )
    def test_high_confidence_typo_autoresolves(self, resolver, query, expected_canonical):
        res = resolver.resolve(query)
        assert res.destination_type == DestinationType.WEBSITE
        assert res.target == expected_canonical
        assert res.url is not None

    def test_medium_confidence_typo_triggers_clarification(self, resolver):
        res = resolver.resolve("open gog")
        assert res.destination_type == DestinationType.AMBIGUOUS
        assert res.suggested_clarification is not None
        assert "Google" in res.suggested_clarification

    def test_nonexistent_desktop_app_fallback(self, resolver):
        res = resolver.resolve("open a nonexistent desktop application")
        assert res.destination_type == DestinationType.APPLICATION
        assert res.is_resolved is False

    def test_get_search_url(self, resolver):
        kaggle_search = resolver.get_search_url("kaggle", "machine learning")
        assert "kaggle.com/search?q=machine+learning" in kaggle_search

        gh_search = resolver.get_search_url("github", "avi")
        assert "github.com/search?q=avi" in gh_search

        default_search = resolver.get_search_url("customsite", "datasets")
        assert "google.com/search?q=datasets" in default_search


class TestDestinationIntents:
    @pytest.mark.parametrize(
        ("prompt", "expected_intent_type", "expected_target_contains"),
        [
            ("open kaggle", AssistantIntentType.OPEN_URL, "kaggle.com"),
            ("open github", AssistantIntentType.OPEN_URL, "github.com"),
            ("open chatgpt", AssistantIntentType.OPEN_URL, "chatgpt.com"),
            ("open youtube", AssistantIntentType.OPEN_URL, "youtube.com"),
            ("open google", AssistantIntentType.OPEN_URL, "google.com"),
            ("open reddit", AssistantIntentType.OPEN_URL, "reddit.com"),
            ("open wikipedia", AssistantIntentType.OPEN_URL, "wikipedia.org"),
            ("open kaggle.com", AssistantIntentType.OPEN_URL, "kaggle.com"),
            ("open https://kaggle.com", AssistantIntentType.OPEN_URL, "kaggle.com"),
            ("open youtub", AssistantIntentType.OPEN_URL, "youtube.com"),
            ("open githb", AssistantIntentType.OPEN_URL, "github.com"),
            ("open chatgptt", AssistantIntentType.OPEN_URL, "chatgpt.com"),
            ("open a nonexistent desktop application", AssistantIntentType.OPEN_APP, "nonexistent"),
        ],
    )
    def test_destination_intent_classification(self, prompt, expected_intent_type, expected_target_contains):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == expected_intent_type
        assert expected_target_contains.lower() in intent.target.lower()

    def test_ambiguous_destination_intent(self):
        intent = detect_assistant_intent("open gog")
        assert intent.intent_type == AssistantIntentType.CLARIFICATION
        assert intent.extra.get("clarification_type") == "ambiguous_destination"


class TestDestinationOrchestrationAndPlanning:
    @pytest.fixture
    def orchestrator(self):
        return AssistantOrchestrator(config=Config())

    def test_open_kaggle_never_reports_not_installed(self, orchestrator):
        """open kaggle must open in browser, never report Application 'Kaggle' is not installed."""
        res = orchestrator.handle("open kaggle", auto_execute_actions=False)
        assert "kaggle.com" in res.text or "Kaggle" in res.text
        assert "not installed" not in res.text.lower()

    def test_nonexistent_desktop_app_reports_not_installed(self, orchestrator):
        """open a nonexistent desktop application reports not installed."""
        res = orchestrator.handle("open a nonexistent desktop application", auto_execute_actions=False)
        assert "not installed" in res.text.lower()

    def test_compound_web_search_plan_synthesis(self):
        """open kaggle and then search for datasets synthesizes a coherent 3-step plan."""
        planner = AgentPlanner()
        plan = planner.create_plan("open kaggle and then search for datasets")
        assert plan is not None
        assert len(plan.steps) == 3

        step1, step2, step3 = plan.steps
        assert step1.capability_name == "browser.navigate"
        assert "kaggle.com" in step1.arguments["url"]
        assert "open" in step1.description.lower()

        assert step2.capability_name == "browser.observe"
        assert "observe" in step2.description.lower()

        assert step3.capability_name == "browser.navigate"
        assert "kaggle.com/search?q=datasets" in step3.arguments["url"]
        assert "search" in step3.description.lower()

    def test_compound_web_search_execution(self, orchestrator):
        """End-to-end execution of compound web search plan."""
        with patch("avi.actions.system.OpenUrlAction.execute", return_value=ActionResult(success=True, message="Navigated")):
            with patch("avi.browser.controller.BrowserController.observe", return_value=BrowserState(url="https://www.kaggle.com", title="Kaggle", browser_name="chrome", status="ready", is_running=True)):
                res = orchestrator.handle("open kaggle and then search for datasets", auto_execute_actions=True)
                assert res.plan is not None
                assert len(res.plan.steps) == 3
                assert all(s.verified for s in res.plan.steps)
                assert "datasets" in res.text.lower()
