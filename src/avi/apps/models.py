"""Data models for application discovery and resolution."""

from dataclasses import dataclass


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
