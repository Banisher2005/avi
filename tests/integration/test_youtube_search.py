"""Comprehensive test suite for Phase 13.0 Native YouTube Search capabilities.

Tests:
1. URL construction safety and sanitization.
2. YouTubeSearchCapability registration and execution.
3. Deterministic intent classification across all phrasing variations.
4. Ambiguous and empty query clarification handling.
5. Multi-turn conversational follow-up.
6. Orchestrator native routing without LLM or shell commands.
7. Preservation of 'open YouTube' as desktop.url.open.
8. CLI end-to-end integration.
"""

import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

from avi.assistant.intents import (
    AssistantIntentType,
    detect_assistant_intent,
)
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry
from avi.capabilities.web.search import (
    YouTubeSearchCapability,
    build_youtube_search_url,
)
from avi.cli import main
from avi.config import Config
from avi.orchestrator.models import ConversationHistory
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.providers.base import BaseProvider, ResponseMetrics


class MockAssistantProvider(BaseProvider):
    """Mock provider to ensure LLM is never called for native searches."""

    def __init__(self):
        self.call_count = 0
        self._metrics = ResponseMetrics(total_duration_ms=10.0)

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        self.call_count += 1
        raise AssertionError(f"LLM generate() should not be called for native search: {prompt}")

    def generate_full(self, prompt, system_prompt=None, context=None):
        self.call_count += 1
        raise AssertionError(
            f"LLM generate_full() should not be called for native search: {prompt}"
        )

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "mock-safety-check"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return [1, 2]


# ---------------------------------------------------------------------------
# 1. URL Building & Sanitization Tests
# ---------------------------------------------------------------------------


class TestYouTubeUrlSafety:
    """Security and formatting tests for build_youtube_search_url."""

    def test_basic_query_encoding(self):
        url = build_youtube_search_url("Linux tutorials")
        assert url == "https://www.youtube.com/results?search_query=Linux+tutorials"

    def test_special_characters_safety(self):
        # Shell characters, spaces, punctuation
        dangerous_query = "linux; rm -rf / && $(whoami) `id` | & $PATH \"quotes' *"
        url = build_youtube_search_url(dangerous_query)
        assert url.startswith("https://www.youtube.com/results?search_query=")
        # Verify query parameters are URL-encoded and safe
        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "www.youtube.com"
        assert parsed.path == "/results"
        query_params = urllib.parse.parse_qs(parsed.query)
        assert query_params["search_query"] == [dangerous_query.strip()]

    def test_unicode_query(self):
        query = "पायथन ट्यूटोरियल 🚀"
        url = build_youtube_search_url(query)
        assert url.startswith("https://www.youtube.com/results?search_query=")
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)
        assert query_params["search_query"] == [query]

    def test_empty_query_raises_value_error(self):
        with pytest.raises(ValueError, match="Search query cannot be empty"):
            build_youtube_search_url("")

        with pytest.raises(ValueError, match="Search query cannot be empty"):
            build_youtube_search_url("   \n\t  ")


# ---------------------------------------------------------------------------
# 2. YouTubeSearchCapability Tests
# ---------------------------------------------------------------------------


class TestYouTubeSearchCapability:
    """Tests for capability registration and execution."""

    def test_registry_registration(self):
        registry = create_default_capability_registry()
        cap = registry.get("web.youtube.search")
        assert cap is not None
        assert isinstance(cap, YouTubeSearchCapability)
        # Check aliases
        assert registry.get("youtube.search") is cap
        assert registry.get("youtube_search") is cap

    def test_successful_execution_delegates_to_url_opener(self):
        mock_opener = MagicMock()
        mock_opener.execute.return_value = MagicMock(
            success=True, error=None, message="URL opened."
        )

        cap = YouTubeSearchCapability(url_capability=mock_opener)
        result = cap.execute(query="Linux kernel architecture")

        assert result.success is True
        assert result.status == ExecutionStatus.SUCCESS
        assert "Searching YouTube for Linux kernel architecture" in result.message
        assert result.data["query"] == "Linux kernel architecture"
        assert (
            result.data["url"]
            == "https://www.youtube.com/results?search_query=Linux+kernel+architecture"
        )

        mock_opener.execute.assert_called_once_with(
            url="https://www.youtube.com/results?search_query=Linux+kernel+architecture"
        )

    def test_empty_query_returns_failed_status_with_clarification(self):
        mock_opener = MagicMock()
        cap = YouTubeSearchCapability(url_capability=mock_opener)
        result = cap.execute(query="   ")

        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert "What would you like me to search for on YouTube?" in result.message
        mock_opener.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Intent Detection Tests
# ---------------------------------------------------------------------------


class TestYouTubeIntentDetection:
    """Test deterministic intent recognition across varied natural phrasing."""

    @pytest.mark.parametrize(
        "phrase, expected_query",
        [
            ("search YouTube for Linux tutorials", "Linux tutorials"),
            ("find Linux tutorials on YouTube", "Linux tutorials"),
            ("look up Linux tutorials on YouTube", "Linux tutorials"),
            ("watch Linux tutorials on YouTube", "Linux tutorials"),
            ("search on YouTube for Linux tutorials", "Linux tutorials"),
            ("YouTube search Linux tutorials", "Linux tutorials"),
            ("search for Linux tutorials on YouTube", "Linux tutorials"),
            ("find videos on YouTube about Linux tutorials", "Linux tutorials"),
            ("find me Linux tutorials on YouTube", "Linux tutorials"),
            ("show me Linux tutorials on YouTube", "Linux tutorials"),
            ("open YouTube and search for Linux tutorials", "Linux tutorials"),
            ("please search YouTube for python async", "python async"),
            ("can you find lo-fi beats on YouTube", "lo-fi beats"),
            ("could you look up rust programming on YouTube", "rust programming"),
            ("search YouTube for 'deep learning'", "deep learning"),
        ],
    )
    def test_youtube_search_phrasing_variations(self, phrase, expected_query):
        intent = detect_assistant_intent(phrase)
        assert intent.intent_type == AssistantIntentType.YOUTUBE_SEARCH
        assert intent.target == expected_query
        assert intent.extra.get("query") == expected_query

    @pytest.mark.parametrize(
        "phrase",
        [
            "search YouTube",
            "search YouTube for",
            "find something on YouTube",
            "search for something on YouTube",
            "look up something on YouTube",
            "search on YouTube",
            "YouTube search",
        ],
    )
    def test_ambiguous_or_empty_queries_require_clarification(self, phrase):
        intent = detect_assistant_intent(phrase)
        assert intent.intent_type == AssistantIntentType.CLARIFICATION
        assert intent.target == "youtube"
        assert "What would you like me to search for on YouTube?" in intent.extra.get("message", "")

    @pytest.mark.parametrize(
        "phrase",
        [
            "open YouTube",
            "please open YouTube",
            "can you open YouTube",
            "could you open YouTube",
            "launch YouTube",
            "run YouTube",
        ],
    )
    def test_open_youtube_preservation(self, phrase):
        """'open YouTube' must open the home page via OPEN_URL, not trigger a search."""
        intent = detect_assistant_intent(phrase)
        assert intent.intent_type == AssistantIntentType.OPEN_URL
        assert intent.target == "https://www.youtube.com"


# ---------------------------------------------------------------------------
# 4. Multi-Turn Clarification Follow-Up Tests
# ---------------------------------------------------------------------------


class TestYouTubeClarificationFollowUp:
    """Multi-turn interaction tests."""

    def test_conversational_follow_up(self):
        # Turn 1: user asks to search youtube vaguely
        t1_intent = detect_assistant_intent("search YouTube")
        assert t1_intent.intent_type == AssistantIntentType.CLARIFICATION

        # Create simulated last turn in history
        history = ConversationHistory()
        history.add_turn(
            user_query="search YouTube",
            intent_type=t1_intent.intent_type.value,
            response_text=t1_intent.extra["message"],
            target="youtube",
        )

        # Turn 2: user answers with search query
        t2_intent = detect_assistant_intent("Linux tutorials", last_turn=history.last_turn)
        assert t2_intent.intent_type == AssistantIntentType.YOUTUBE_SEARCH
        assert t2_intent.target == "Linux tutorials"
        assert t2_intent.extra.get("query") == "Linux tutorials"


# ---------------------------------------------------------------------------
# 5. Assistant Orchestrator End-to-End Tests
# ---------------------------------------------------------------------------


class TestOrchestratorYouTubeSearch:
    """Orchestrator integration tests."""

    @pytest.fixture
    def orchestrator(self):
        mock_provider = MockAssistantProvider()
        cfg = Config(provider="ollama")
        from avi.core.router import Router

        router = Router(cfg, provider=mock_provider)
        orc = AssistantOrchestrator(config=cfg, router=router)
        return orc

    def test_orchestrator_routes_youtube_search_without_llm(self, orchestrator):
        with patch.object(
            orchestrator.capabilities.get("web.youtube.search").url_capability, "execute"
        ) as mock_url_open:
            mock_url_open.return_value = MagicMock(success=True, error=None, message="URL opened.")

            res = orchestrator.handle("search YouTube for Linux tutorials")

            assert res is not None
            assert res.command_request is None  # Zero shell commands generated
            assert "Searching YouTube for Linux tutorials." in res.text
            assert res.capability_result is not None
            assert res.capability_result.success is True
            assert (
                res.capability_result.data["url"]
                == "https://www.youtube.com/results?search_query=Linux+tutorials"
            )

    def test_orchestrator_handles_empty_search_clarification(self, orchestrator):
        res = orchestrator.handle("search YouTube")
        assert "What would you like me to search for on YouTube?" in res.text
        assert res.command_request is None

    def test_orchestrator_multi_turn_flow(self, orchestrator):
        with patch.object(
            orchestrator.capabilities.get("web.youtube.search").url_capability, "execute"
        ) as mock_url_open:
            mock_url_open.return_value = MagicMock(success=True, error=None, message="URL opened.")

            # Turn 1
            res1 = orchestrator.handle("search YouTube")
            assert "What would you like me to search for on YouTube?" in res1.text

            # Turn 2
            res2 = orchestrator.handle("Linux tutorials")
            assert "Searching YouTube for Linux tutorials." in res2.text
            assert res2.capability_result is not None
            assert res2.capability_result.success is True

    def test_orchestrator_preserves_open_youtube(self, orchestrator):
        with patch("avi.actions.system.webbrowser.open") as mock_browser_open:
            mock_browser_open.return_value = True

            res = orchestrator.handle("open YouTube")
            assert "https://www.youtube.com" in res.text
            assert res.command_request is None
            mock_browser_open.assert_called_once_with("https://www.youtube.com")


# ---------------------------------------------------------------------------
# 6. CLI Routing Integration Tests
# ---------------------------------------------------------------------------


class TestCliYouTubeSearchRouting:
    """CLI-level verification of YouTube search routing."""

    def test_cli_search_youtube_routes_to_native_capability(self, capsys):
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() should not be called for YouTube search intent"
        )

        with (
            patch(
                "avi.config.Config.load", return_value=Config(provider="ollama", show_timing=False)
            ),
            patch("avi.cli.Router", return_value=mock_router),
            patch("avi.actions.system.webbrowser.open", return_value=True) as mock_open,
        ):
            code = main(["search YouTube for Linux tutorials"])

        assert code == 0
        captured = capsys.readouterr()
        assert "Searching YouTube for Linux tutorials." in captured.out
        assert "sudo" not in captured.out
        assert "amixer" not in captured.out
        assert "[Blocked:" not in captured.out
        mock_open.assert_called_once_with(
            "https://www.youtube.com/results?search_query=Linux+tutorials"
        )

    def test_cli_open_youtube_preserves_home_page(self, capsys):
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() should not be called for open YouTube intent"
        )

        with (
            patch(
                "avi.config.Config.load", return_value=Config(provider="ollama", show_timing=False)
            ),
            patch("avi.cli.Router", return_value=mock_router),
            patch("avi.actions.system.webbrowser.open", return_value=True) as mock_open,
        ):
            code = main(["open YouTube"])

        assert code == 0
        captured = capsys.readouterr()
        assert "https://www.youtube.com" in captured.out
        mock_open.assert_called_once_with("https://www.youtube.com")
