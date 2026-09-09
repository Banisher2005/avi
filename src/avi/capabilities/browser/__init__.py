"""Browser capabilities for AVI."""

from avi.capabilities.browser.click import BrowserClickCapability
from avi.capabilities.browser.download import BrowserDownloadCapability
from avi.capabilities.browser.extract import BrowserExtractCapability
from avi.capabilities.browser.input import BrowserTypeCapability
from avi.capabilities.browser.navigate import (
    BrowserNavigateCapability,
    sanitize_and_validate_url,
)
from avi.capabilities.browser.observe import BrowserObserveCapability
from avi.capabilities.browser.scroll import BrowserScrollCapability

__all__ = [
    "BrowserObserveCapability",
    "BrowserNavigateCapability",
    "BrowserExtractCapability",
    "BrowserTypeCapability",
    "BrowserClickCapability",
    "BrowserScrollCapability",
    "BrowserDownloadCapability",
    "sanitize_and_validate_url",
]
