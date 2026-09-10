"""Browser interaction and computer use package for AVI."""

from avi.browser.cdp import CdpClient
from avi.browser.controller import BrowserController
from avi.browser.models import BrowserObservation, BrowserState, InteractiveElement, TabInfo

__all__ = [
    "BrowserState",
    "BrowserObservation",
    "InteractiveElement",
    "TabInfo",
    "CdpClient",
    "BrowserController",
]
