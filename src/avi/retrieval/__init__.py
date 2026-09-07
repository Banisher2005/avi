"""Retrieval subsystem for AVI desktop assistant."""

from avi.retrieval.base import BaseSearchProvider
from avi.retrieval.models import SearchOptions, SearchResult, SearchResults
from avi.retrieval.youtube import (
    YouTubeSearchRetrievalProvider,
    parse_duration_seconds,
    validate_youtube_url,
)

__all__ = [
    "BaseSearchProvider",
    "SearchResult",
    "SearchResults",
    "SearchOptions",
    "YouTubeSearchRetrievalProvider",
    "validate_youtube_url",
    "parse_duration_seconds",
]
