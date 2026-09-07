"""Abstract base search retrieval provider."""

from abc import ABC, abstractmethod

from avi.retrieval.models import SearchOptions, SearchResults


class BaseSearchProvider(ABC):
    """Provider-independent interface for external information retrieval."""

    source_name: str = "web"

    @abstractmethod
    def search(self, query: str, options: SearchOptions | None = None) -> SearchResults:
        """Execute a search query and return normalized results."""
        pass

    @abstractmethod
    def validate_url(self, url: str) -> bool:
        """Validate that a URL returned from this provider is safe and well-formed."""
        pass
