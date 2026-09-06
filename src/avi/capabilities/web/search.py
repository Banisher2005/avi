"""Web search capabilities for AVI desktop assistant."""

import logging
import urllib.parse
from typing import Any

from avi.capabilities.desktop.app_launcher import OpenUrlCapability
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger("avi.capabilities.web")


def build_youtube_search_url(query: str) -> str:
    """Safely construct an HTTPS YouTube search URL with encoded query parameters.

    Ensures that special characters, unicode, spaces, ampersands, and quotes
    are safely encoded and cannot alter the destination origin or protocol.
    """
    clean_query = query.strip()
    if not clean_query:
        raise ValueError("Search query cannot be empty.")

    # quote_plus safely encodes spaces as '+', and encodes '&', ';', '|', quotes, unicode, etc.
    encoded_query = urllib.parse.quote_plus(clean_query)
    return f"https://www.youtube.com/results?search_query={encoded_query}"


class BaseWebSearchCapability(BaseCapability):
    """Abstract base capability for web-based search engines."""

    service_name: str = "Web"
    risk_category = ActionCategory.EXTERNAL_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(self, url_capability: BaseCapability | None = None) -> None:
        self.url_capability = url_capability or OpenUrlCapability()

    def build_search_url(self, query: str) -> str:
        """Construct the search URL for this specific search service."""
        raise NotImplementedError

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Validate query, construct URL, and delegate to desktop URL opener."""
        raw_query = kwargs.get("query")
        if raw_query is None or not str(raw_query).strip():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Search query cannot be empty.",
                message=f"What would you like me to search for on {self.service_name}?",
            )

        clean_query = str(raw_query).strip().strip("\"'")
        if not clean_query:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Search query cannot be empty.",
                message=f"What would you like me to search for on {self.service_name}?",
            )

        try:
            search_url = self.build_search_url(clean_query)
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to build search URL: {err}",
            )

        # Delegate opening to the centralized desktop URL opener
        open_result = self.url_capability.execute(url=search_url)
        if not open_result.success:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=open_result.error or open_result.message,
                message=f"Could not open {self.service_name} search: {open_result.error or open_result.message}",
                data={"query": clean_query, "url": search_url},
            )

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Searching {self.service_name} for {clean_query}.",
            data={
                "query": clean_query,
                "url": search_url,
                "service": self.service_name.lower(),
            },
        )


class YouTubeSearchCapability(BaseWebSearchCapability):
    """Search YouTube for videos and playlists matching a user query."""

    name = "web.youtube.search"
    aliases = ["youtube.search", "youtube_search"]
    service_name = "YouTube"
    description = "Search YouTube for videos matching a given query."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term or query to search on YouTube",
            },
        },
        "required": ["query"],
    }

    def build_search_url(self, query: str) -> str:
        return build_youtube_search_url(query)


class YouTubeSearchResultsCapability(BaseCapability):
    """Retrieve structured YouTube search results without opening the browser."""

    name = "web.youtube.search_results"
    aliases = ["youtube.search_results", "youtube_search_results", "youtube.retrieve"]
    description = "Retrieve structured video search results from YouTube for ranking or reasoning."
    risk_category = ActionCategory.EXTERNAL_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term or topic to search on YouTube",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to retrieve (default: 10)",
            },
        },
        "required": ["query"],
    }

    def __init__(self, retrieval_provider: Any | None = None) -> None:
        from avi.retrieval.youtube import YouTubeSearchRetrievalProvider

        self.retrieval_provider = retrieval_provider or YouTubeSearchRetrievalProvider()

    def execute(self, **kwargs: Any) -> CapabilityResult:
        query = kwargs.get("query")
        if not query or not str(query).strip():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Search query cannot be empty.",
                message="What would you like me to search for on YouTube?",
            )

        clean_query = str(query).strip().strip("\"'")
        limit = kwargs.get("limit", 10)
        try:
            limit_int = int(limit)
        except (ValueError, TypeError):
            limit_int = 10

        from avi.retrieval.models import SearchOptions

        options = SearchOptions(max_results=limit_int)
        search_res = self.retrieval_provider.search(clean_query, options=options)

        if search_res.error and search_res.is_empty:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=search_res.error,
                message=f"Could not retrieve YouTube results: {search_res.error}",
                data={"query": clean_query, "results": []},
            )

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=f"Retrieved {len(search_res.results)} YouTube results for '{clean_query}'.",
            data={
                "query": clean_query,
                "count": len(search_res.results),
                "results": [r.to_dict() for r in search_res.results],
                "search_results": search_res.results,
            },
        )
