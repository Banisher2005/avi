"""Normalized data models for search and content retrieval."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """Normalized metadata for an individual search result item."""

    id: str
    title: str
    url: str
    source: str
    description: str = ""
    channel: str | None = None
    thumbnail_url: str | None = None
    published_at: str | None = None
    duration: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert SearchResult into a serializable dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "description": self.description,
            "channel": self.channel,
            "thumbnail_url": self.thumbnail_url,
            "published_at": self.published_at,
            "duration": self.duration,
            "metadata": self.metadata,
        }

    def format_summary(self) -> str:
        """Format a single-line summary of this result."""
        dur = f" ({self.duration})" if self.duration else ""
        ch = f" — {self.channel}" if self.channel else ""
        return f"{self.title}{ch}{dur}"


@dataclass
class SearchResults:
    """Collection of search results with query and error status."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    total_found: int = 0
    source: str = ""
    error: str | None = None

    @property
    def is_empty(self) -> bool:
        """Check if search results are empty."""
        return len(self.results) == 0

    def to_dict(self) -> dict[str, Any]:
        """Convert SearchResults to dictionary."""
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "total_found": self.total_found,
            "source": self.source,
            "error": self.error,
        }


@dataclass
class SearchOptions:
    """Search query options and execution constraints."""

    max_results: int = 10
    timeout: float = 6.0
    api_key: str | None = None
