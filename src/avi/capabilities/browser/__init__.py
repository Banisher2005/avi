"""Browser capabilities for AVI."""

from avi.capabilities.browser.click import BrowserClickCapability
from avi.capabilities.browser.download import BrowserDownloadCapability
from avi.capabilities.browser.extract import BrowserExtractCapability
from avi.capabilities.browser.input import BrowserTypeCapability
from avi.capabilities.browser.keyboard import BrowserPressKeyCapability
from avi.capabilities.browser.navigate import (
    BrowserNavigateCapability,
    sanitize_and_validate_url,
)
from avi.capabilities.browser.observe import BrowserObserveCapability
from avi.capabilities.browser.scroll import BrowserScrollCapability
from avi.capabilities.browser.tabs import BrowserTabsCapability

__all__ = [
    "BrowserObserveCapability",
    "BrowserNavigateCapability",
    "BrowserExtractCapability",
    "BrowserTypeCapability",
    "BrowserPressKeyCapability",
    "BrowserClickCapability",
    "BrowserScrollCapability",
    "BrowserDownloadCapability",
    "BrowserTabsCapability",
    "sanitize_and_validate_url",
]
