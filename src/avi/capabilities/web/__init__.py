"""Web navigation and search capabilities."""

from avi.capabilities.web.search import (
    BaseWebSearchCapability,
    YouTubeSearchCapability,
    build_youtube_search_url,
)

__all__ = [
    "BaseWebSearchCapability",
    "YouTubeSearchCapability",
    "build_youtube_search_url",
]
