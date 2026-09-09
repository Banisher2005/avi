"""Data models for application discovery and resolution."""

from dataclasses import dataclass
from enum import Enum


class DestinationType(str, Enum):
    """Classification for user requested destinations."""

    APPLICATION = "application"
    WEBSITE = "website"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ApplicationResolution:
    """Structured information about an identified application and its executable."""

    requested_name: str
    canonical_name: str
    executable: str | None
    desktop_entry: str | None
    platform: str
    installed: bool
    confidence: float

    @property
    def is_resolved(self) -> bool:
        """True if application was successfully resolved to an installed executable."""
        return self.installed and self.executable is not None


@dataclass
class DestinationResolution:
    """Structured resolution output for destinations (apps, websites, URLs)."""

    query: str
    target: str
    destination_type: DestinationType
    url: str | None = None
    app_resolution: ApplicationResolution | None = None
    browser: str | None = None
    confidence: float = 1.0
    suggested_clarification: str | None = None
    is_explicit_app: bool = False
    is_explicit_web: bool = False

    @property
    def is_resolved(self) -> bool:
        """Whether this destination was confidently resolved."""
        if self.destination_type == DestinationType.WEBSITE:
            return bool(self.url)
        if self.destination_type == DestinationType.APPLICATION:
            return bool(self.app_resolution and self.app_resolution.is_resolved)
        return False
