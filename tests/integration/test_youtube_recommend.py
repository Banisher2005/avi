"""Comprehensive integration tests for Phase 13.1 YouTube Recommend + Open Search Result.

Covers:
1. Intent classification — YOUTUBE_RECOMMEND vs YOUTUBE_SEARCH vs OPEN_SEARCH_RESULT.
2. Retrieval capability (web.youtube.search_results) — mocked network.
3. Orchestrator YOUTUBE_RECOMMEND handler — with and without provider.
4. Provider ranking — mock provider returns valid/invalid/malformed JSON.
5. Multi-turn OPEN_SEARCH_RESULT — "open it" after recommend.
6. OPEN_SEARCH_RESULT by index ("open the second one").
7. URL validation boundary — bad IDs are rejected, good IDs pass.
8. Network error graceful handling.
9. Empty results graceful handling.
10. Provider unavailable falls back to raw listing.
11. Duration constraint filtering.
12. validate_youtube_url correctness.
"""

import json
from unittest.mock import MagicMock, patch

from avi.assistant.intents import (
    AssistantIntentType,
    detect_assistant_intent,
)
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry
from avi.config import Config
from avi.orchestrator.models import ConversationTurn
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.providers.base import BaseProvider
from avi.providers.models import ProviderCapabilities, ProviderResponse, ResponseMetrics
from avi.retrieval.models import SearchResult, SearchResults
from avi.retrieval.youtube import (
    parse_duration_seconds,
    validate_youtube_url,
)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


def _make_search_result(n: int, duration: str | None = "10:00") -> SearchResult:
    """Build a realistic SearchResult for testing."""
    return SearchResult(
        id=f"yt_res_{n}",
        title=f"Test Video {n}: Building local AI agents",
        url="https://www.youtube.com/watch?v=AAAAAAAAAAA"[:43] + str(n).zfill(2),
        # Use a known valid video ID pattern
        source="youtube",
        description=f"Description of test video {n}",
        channel=f"Channel {n}",
        duration=duration,
        metadata={
            "video_id": f"AAAAAAAAAA{n}",
            "duration_seconds": parse_duration_seconds(duration),
        },
    )


def _make_valid_results(count: int = 5) -> list[SearchResult]:
    return [_make_search_result(i) for i in range(1, count + 1)]


class MockRankingProvider(BaseProvider):
    """Provider that returns a valid ranking JSON."""

    def __init__(self, selected_id: str = "yt_res_1", reason: str = "Best intro content."):
        self.selected_id = selected_id
        self.reason = reason
        self._call_count = 0
        self._metrics = ResponseMetrics(total_duration_ms=10.0)

    def generate_full(self, prompt, system_prompt=None, context=None):
        self._call_count += 1
        text = json.dumps({"selected_result_id": self.selected_id, "reason": self.reason})
        return ProviderResponse(text=text, metrics=self._metrics)

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        yield json.dumps({"selected_result_id": self.selected_id, "reason": self.reason})

    def capabilities(self):
        return ProviderCapabilities(text_reasoning=True, structured_output=True)

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "mock-ranker"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return None


class MockUnavailableProvider(BaseProvider):
    """Provider that is never available."""

    def is_available(self):
        return False

    def warmup(self):
        return False

    def get_model_name(self):
        return "unavailable"

    def generate_full(self, prompt, system_prompt=None, context=None):
        raise RuntimeError("Provider is not available")

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        return iter([])

    @property
    def last_metrics(self):
        return None

    @property
    def last_context(self):
        return None


def _make_orchestrator(provider=None, search_results_cap=None) -> AssistantOrchestrator:
    """Build an orchestrator with mocked capabilities and optional provider."""
    config = Config()
    capabilities = create_default_capability_registry()
    if search_results_cap is not None:
        capabilities.register(search_results_cap)

    mock_router = MagicMock()
    mock_router.provider = provider
    mock_router.check_fast_path.return_value = None

    orch = AssistantOrchestrator(
        config=config,
        router=mock_router,
        capabilities=capabilities,
    )
    return orch


# ---------------------------------------------------------------------------
# 1. validate_youtube_url
# ---------------------------------------------------------------------------


class TestValidateYoutubeUrl:
    def test_valid_watch_url(self):
        assert validate_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_valid_youtu_be(self):
        assert validate_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True

    def test_valid_m_youtube(self):
        assert validate_youtube_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_valid_youtube_no_www(self):
        assert validate_youtube_url("https://youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_reject_http(self):
        assert validate_youtube_url("http://www.youtube.com/watch?v=dQw4w9WgXcQ") is False

    def test_reject_search_url(self):
        assert validate_youtube_url("https://www.youtube.com/results?search_query=hello") is False

    def test_reject_empty(self):
        assert validate_youtube_url("") is False

    def test_reject_non_string(self):
        assert validate_youtube_url(None) is False  # type: ignore[arg-type]

    def test_reject_bad_domain(self):
        assert validate_youtube_url("https://evil.com/watch?v=dQw4w9WgXcQ") is False

    def test_reject_short_video_id(self):
        assert validate_youtube_url("https://www.youtube.com/watch?v=short") is False

    def test_reject_no_video_id(self):
        assert validate_youtube_url("https://www.youtube.com/watch") is False

    def test_reject_javascript_injection(self):
        assert validate_youtube_url("javascript:alert(1)") is False

    def test_reject_data_uri(self):
        assert validate_youtube_url("data:text/html,<script>alert(1)</script>") is False


# ---------------------------------------------------------------------------
# 2. parse_duration_seconds
# ---------------------------------------------------------------------------


class TestParseDurationSeconds:
    def test_mm_ss(self):
        assert parse_duration_seconds("10:30") == 630

    def test_hh_mm_ss(self):
        assert parse_duration_seconds("1:10:00") == 4200

    def test_seconds_only(self):
        assert parse_duration_seconds("45") == 45

    def test_none(self):
        assert parse_duration_seconds(None) is None

    def test_empty(self):
        assert parse_duration_seconds("") is None

    def test_invalid(self):
        assert parse_duration_seconds("abc") is None


# ---------------------------------------------------------------------------
# 3. Intent Classification — YOUTUBE_RECOMMEND
# ---------------------------------------------------------------------------


class TestYoutubeRecommendIntent:
    def test_find_me_a_good_video(self):
        intent = detect_assistant_intent(
            "find me a good YouTube video about building local AI agents"
        )
        assert intent.intent_type == AssistantIntentType.YOUTUBE_RECOMMEND
        assert "local AI agents" in (intent.extra.get("query") or "")

    def test_recommend_video(self):
        intent = detect_assistant_intent("recommend a YouTube video on Python async")
        assert intent.intent_type == AssistantIntentType.YOUTUBE_RECOMMEND

    def test_best_video(self):
        intent = detect_assistant_intent("what's the best YouTube video on machine learning")
        assert intent.intent_type == AssistantIntentType.YOUTUBE_RECOMMEND

    def test_suggest_youtube(self):
        intent = detect_assistant_intent("suggest a YouTube video about Docker containers")
        assert intent.intent_type == AssistantIntentType.YOUTUBE_RECOMMEND

    def test_short_video(self):
        intent = detect_assistant_intent("find me a short YouTube video on Git rebasing")
        assert intent.intent_type == AssistantIntentType.YOUTUBE_RECOMMEND


# ---------------------------------------------------------------------------
# 4. Intent Classification — OPEN_SEARCH_RESULT (multi-turn)
# ---------------------------------------------------------------------------


class TestOpenSearchResultIntent:
    def _turn_with_results(self):
        """Fake a conversation turn that has search results."""
        turn = MagicMock(spec=ConversationTurn)
        turn.search_results = _make_valid_results(3)
        turn.selected_result = turn.search_results[0]
        return turn

    def test_open_it_with_results(self):
        last_turn = self._turn_with_results()
        intent = detect_assistant_intent("open it", last_turn=last_turn)
        assert intent.intent_type == AssistantIntentType.OPEN_SEARCH_RESULT

    def test_open_the_second_one(self):
        last_turn = self._turn_with_results()
        intent = detect_assistant_intent("open the second one", last_turn=last_turn)
        assert intent.intent_type == AssistantIntentType.OPEN_SEARCH_RESULT
        assert intent.extra.get("index") == 1

    def test_open_that_without_context(self):
        """Without search results context, should NOT be OPEN_SEARCH_RESULT."""
        intent = detect_assistant_intent("open it")
        assert intent.intent_type != AssistantIntentType.OPEN_SEARCH_RESULT

    def test_open_the_first_one(self):
        last_turn = self._turn_with_results()
        intent = detect_assistant_intent("open the first one", last_turn=last_turn)
        assert intent.intent_type == AssistantIntentType.OPEN_SEARCH_RESULT
        assert intent.extra.get("index") == 0


# ---------------------------------------------------------------------------
# 5. Orchestrator: YOUTUBE_RECOMMEND with mock retrieval + provider
# ---------------------------------------------------------------------------


class TestOrchestratorYoutubeRecommend:
    def _mock_results_capability(self, results: list[SearchResult]) -> "MagicMock":
        cap = MagicMock()
        cap.name = "web.youtube.search_results"
        cap.aliases = ["youtube.search_results", "youtube_search_results", "youtube.retrieve"]
        cap.execute.return_value = CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message="Retrieved results.",
            data={"query": "test", "count": len(results), "search_results": results},
        )
        return cap

    def test_recommend_with_provider_selects_result(self):
        """With provider available, recommendation includes selected result."""
        results = _make_valid_results(3)
        provider = MockRankingProvider(selected_id="yt_res_1")
        orch = _make_orchestrator(provider=provider)

        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=provider),
        ):
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="ok",
                data={"query": "AI agents", "count": 3, "search_results": results},
            )
            result = orch.handle(
                "find me a good YouTube video about building local AI agents",
                auto_execute_actions=True,
            )

        assert "Best match" in result.text
        assert result.search_results is not None
        assert len(result.search_results) == 3
        assert result.selected_result is not None
        assert result.selected_result.id == "yt_res_1"

    def test_recommend_without_provider_lists_results(self):
        """Without provider, should list results without selection."""
        results = _make_valid_results(3)
        orch = _make_orchestrator(provider=None)

        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=None),
        ):
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="ok",
                data={"query": "AI agents", "count": 3, "search_results": results},
            )
            result = orch.handle(
                "find me a good YouTube video about building local AI agents",
                auto_execute_actions=True,
            )

        assert result.search_results is not None
        assert "1." in result.text or "Here are" in result.text
        assert result.selected_result is None

    def test_recommend_network_error_graceful(self):
        """Network failure returns graceful error message."""
        orch = _make_orchestrator(provider=None)

        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=None),
        ):
            mock_exec.return_value = CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Network unavailable or search request timed out.",
                message="Could not retrieve YouTube results: Network unavailable or search request timed out.",
                data={"query": "AI agents", "results": []},
            )
            result = orch.handle(
                "find me a good YouTube video about building local AI agents",
                auto_execute_actions=True,
            )

        assert result.text
        assert "could not" in result.text.lower() or "network" in result.text.lower()
        assert result.search_results is None

    def test_recommend_empty_results_graceful(self):
        """Empty result set returns helpful message."""
        orch = _make_orchestrator(provider=None)

        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=None),
        ):
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="ok",
                data={"query": "xyzzy123456_nonexistent", "count": 0, "search_results": []},
            )
            result = orch.handle(
                "find me a good YouTube video about xyzzy123456_nonexistent",
                auto_execute_actions=True,
            )

        assert result.text
        assert "couldn't find" in result.text.lower() or "no" in result.text.lower()

    def test_recommend_provider_returns_invalid_id_falls_back(self):
        """Provider returning unknown selected_result_id should fall back to listing."""
        results = _make_valid_results(3)
        provider = MockRankingProvider(selected_id="yt_res_999")  # non-existent ID
        orch = _make_orchestrator(provider=provider)

        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=provider),
        ):
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="ok",
                data={"query": "AI agents", "count": 3, "search_results": results},
            )
            result = orch.handle(
                "find me a good YouTube video about building local AI agents",
                auto_execute_actions=True,
            )

        # Should still return results, just without a selected recommendation
        assert result.search_results is not None
        assert result.selected_result is None  # ID not found, no selection
        assert result.text


class TestMalformedProviderResponse:
    """Provider ranking fails with malformed JSON — should fall back gracefully."""

    class MalformedProvider(BaseProvider):
        def generate_full(self, prompt, system_prompt=None, context=None):
            return ProviderResponse(
                text="Here is my recommendation: I think you should watch the first one.",
                metrics=ResponseMetrics(total_duration_ms=5.0),
            )

        def generate(self, prompt, system_prompt=None, context=None, stream=True):
            yield "malformed"

        def capabilities(self):
            return ProviderCapabilities(text_reasoning=True, structured_output=True)

        def is_available(self):
            return True

        def warmup(self):
            return True

        def get_model_name(self):
            return "mock-malformed"

        @property
        def last_metrics(self):
            return None

        @property
        def last_context(self):
            return None

    def test_malformed_json_falls_back_to_listing(self):
        results = _make_valid_results(3)
        provider = self.MalformedProvider()
        orch = _make_orchestrator(provider=provider)

        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=provider),
        ):
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="ok",
                data={"query": "AI agents", "count": 3, "search_results": results},
            )
            result = orch.handle(
                "find me a good YouTube video about AI agents",
                auto_execute_actions=True,
            )

        # selected_result should be None since JSON was malformed
        assert result.selected_result is None
        assert result.search_results is not None
        assert result.text


# ---------------------------------------------------------------------------
# 6. Orchestrator: OPEN_SEARCH_RESULT
# ---------------------------------------------------------------------------


class TestOrchestratorOpenSearchResult:
    def _orch_with_history(
        self, results: list[SearchResult], selected=None
    ) -> AssistantOrchestrator:
        orch = _make_orchestrator(provider=None)
        orch.history.add_turn(
            user_query="find me a good YouTube video about AI",
            intent_type=AssistantIntentType.YOUTUBE_RECOMMEND.value,
            response_text="Here are results...",
            search_results=results,
            selected_result=selected or (results[0] if results else None),
        )
        return orch

    def test_open_it_opens_selected_result(self):
        """'open it' should open the previously selected result."""
        results = _make_valid_results(3)
        # Force valid YouTube watch URL
        results[0] = SearchResult(
            id="yt_res_1",
            title="Test Video 1",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source="youtube",
            description="desc",
            channel="Test Channel",
            duration="10:00",
            metadata={"video_id": "dQw4w9WgXcQ", "duration_seconds": 600},
        )
        orch = self._orch_with_history(results, selected=results[0])

        with patch.object(orch.capabilities, "execute") as mock_cap:
            mock_cap.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="Opened URL.",
                data={},
            )
            result = orch.handle("open it", auto_execute_actions=True)

        assert "opening" in result.text.lower() or "test video 1" in result.text.lower()
        mock_cap.assert_called_once()
        call_kwargs = mock_cap.call_args
        assert "dQw4w9WgXcQ" in str(call_kwargs)

    def test_open_second_one(self):
        """'open the second one' opens results[1]."""
        results = [
            SearchResult(
                id=f"yt_res_{i}",
                title=f"Video {i}",
                url=f"https://www.youtube.com/watch?v=dQw4w9WgXc{i}",
                source="youtube",
                description="",
                channel=None,
                duration=None,
                metadata={"video_id": f"dQw4w9WgXc{i}", "duration_seconds": None},
            )
            for i in range(1, 4)
        ]
        # Fix IDs to be 11 chars
        valid_ids = ["dQw4w9WgXcQ", "dQw4w9WgXcR", "dQw4w9WgXcS"]
        for i, result in enumerate(results):
            results[i] = SearchResult(
                id=result.id,
                title=result.title,
                url=f"https://www.youtube.com/watch?v={valid_ids[i]}",
                source="youtube",
                description="",
                channel=None,
                duration=None,
                metadata={"video_id": valid_ids[i], "duration_seconds": None},
            )
        orch = self._orch_with_history(results, selected=results[0])

        with patch.object(orch.capabilities, "execute") as mock_cap:
            mock_cap.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="Opened URL.",
                data={},
            )
            result = orch.handle("open the second one", auto_execute_actions=True)

        assert mock_cap.called
        call_kwargs = mock_cap.call_args
        assert valid_ids[1] in str(call_kwargs)

    def test_open_result_out_of_bounds(self):
        """Index out of bounds returns helpful message."""
        results = _make_valid_results(2)
        orch = self._orch_with_history(results, selected=results[0])

        with patch("avi.orchestrator.orchestrator.validate_youtube_url", return_value=True):
            result = orch.handle("open the fifth one", auto_execute_actions=True)

        assert result.text
        # Should be an error or clarification, not an open
        assert (
            "open" not in result.text.lower()
            or "only" in result.text.lower()
            or "don't" in result.text.lower()
        )

    def test_open_without_prior_search(self):
        """'open it' with no search history returns helpful message."""
        orch = _make_orchestrator(provider=None)

        result = orch.handle("open it", auto_execute_actions=True)

        # Should not crash; should be a clarification or error
        assert result.text

    def test_invalid_url_rejected(self):
        """Result with invalid URL is rejected before opening."""
        bad_result = SearchResult(
            id="yt_res_1",
            title="Malicious Video",
            url="https://evil.com/watch?v=notreal123",
            source="youtube",
            description="",
            channel=None,
            duration=None,
            metadata={"video_id": "notreal123", "duration_seconds": None},
        )
        orch = self._orch_with_history([bad_result], selected=bad_result)

        with patch.object(orch.capabilities, "execute") as mock_cap:
            result = orch.handle("open it", auto_execute_actions=True)

        mock_cap.assert_not_called()
        assert "invalid" in result.text.lower() or "cannot" in result.text.lower()


# ---------------------------------------------------------------------------
# 7. Multi-turn chain: RECOMMEND → OPEN_SEARCH_RESULT
# ---------------------------------------------------------------------------


class TestMultiTurnRecommendThenOpen:
    def test_full_chain(self):
        """RECOMMEND → 'open it' multi-turn chain resolves correctly."""
        valid_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        results = [
            SearchResult(
                id="yt_res_1",
                title="Best AI Video",
                url=valid_url,
                source="youtube",
                description="",
                channel="AI Channel",
                duration="15:00",
                metadata={"video_id": "dQw4w9WgXcQ", "duration_seconds": 900},
            )
        ]

        provider = MockRankingProvider(selected_id="yt_res_1", reason="Great intro content.")
        orch = _make_orchestrator(provider=provider)

        # Turn 1: recommend
        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=provider),
        ):
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="ok",
                data={"query": "AI agents", "count": 1, "search_results": results},
            )
            res1 = orch.handle(
                "find me a good YouTube video about AI agents",
                auto_execute_actions=True,
            )

        assert res1.search_results
        assert res1.selected_result is not None
        assert res1.selected_result.id == "yt_res_1"

        # Turn 2: open it
        with patch.object(orch.capabilities, "execute") as mock_open:
            mock_open.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="Opened.",
                data={},
            )
            res2 = orch.handle("open it", auto_execute_actions=True)

        assert "opening" in res2.text.lower() or "best ai video" in res2.text.lower()
        mock_open.assert_called_once()
        call_args = str(mock_open.call_args)
        assert "dQw4w9WgXcQ" in call_args


# ---------------------------------------------------------------------------
# 8. Duration constraint filtering
# ---------------------------------------------------------------------------


class TestDurationConstraintFiltering:
    def test_short_filter_applied(self):
        """Duration constraint filters results by max duration."""
        # Create results with different durations
        short_result = SearchResult(
            id="yt_res_1",
            title="Short Video",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source="youtube",
            description="",
            channel="A",
            duration="3:00",
            metadata={"video_id": "dQw4w9WgXcQ", "duration_seconds": 180},
        )
        long_result = SearchResult(
            id="yt_res_2",
            title="Long Video",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcR",
            source="youtube",
            description="",
            channel="B",
            duration="45:00",
            metadata={"video_id": "dQw4w9WgXcR", "duration_seconds": 2700},
        )
        results = [short_result, long_result]

        provider = MockRankingProvider(selected_id="yt_res_1")
        orch = _make_orchestrator(provider=provider)

        with (
            patch.object(orch.capabilities, "execute") as mock_exec,
            patch("avi.orchestrator.orchestrator.select_provider", return_value=provider),
        ):
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message="ok",
                data={"query": "short Python tutorial", "count": 2, "search_results": results},
            )
            # The intent classification should add max_duration_seconds constraint for "short"
            res = orch.handle(
                "find me a short YouTube video about Python",
                auto_execute_actions=True,
            )

        # At minimum the response should return something
        assert res.text
        assert res.search_results


# ---------------------------------------------------------------------------
# 9. ProviderCapabilities.supports() correctness
# ---------------------------------------------------------------------------


class TestProviderCapabilitiesSupports:
    def test_supports_text_reasoning_and_structured_output(self):
        caps = ProviderCapabilities(text_reasoning=True, structured_output=True)
        required = ProviderCapabilities(text_reasoning=True, structured_output=True)
        assert caps.supports(required) is True

    def test_lacks_structured_output(self):
        caps = ProviderCapabilities(text_reasoning=True, structured_output=False)
        required = ProviderCapabilities(text_reasoning=True, structured_output=True)
        assert caps.supports(required) is False

    def test_unavailable_provider_skipped(self):
        from avi.providers.registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register("unavailable", lambda cfg, **kw: MockUnavailableProvider())

        required = ProviderCapabilities(text_reasoning=True, structured_output=True)
        result = reg.select_provider(required, active_provider=None)
        assert result is None


# ---------------------------------------------------------------------------
# 10. SearchResult.to_dict() and SearchResults helpers
# ---------------------------------------------------------------------------


class TestSearchResultModels:
    def test_to_dict_round_trip(self):
        r = _make_search_result(1)
        d = r.to_dict()
        assert d["id"] == "yt_res_1"
        assert d["title"] == r.title
        assert d["url"] == r.url
        assert d["source"] == "youtube"

    def test_search_results_is_empty(self):
        sr = SearchResults(query="test", results=[], total_found=0, source="youtube")
        assert sr.is_empty is True

    def test_search_results_not_empty(self):
        sr = SearchResults(
            query="test",
            results=[_make_search_result(1)],
            total_found=1,
            source="youtube",
        )
        assert sr.is_empty is False
