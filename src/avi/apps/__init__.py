"""Application discovery and resolution package for AVI."""

from avi.apps.models import ApplicationResolution, DestinationResolution, DestinationType
from avi.apps.resolver import ApplicationResolver

__all__ = [
    "ApplicationResolution",
    "ApplicationResolver",
    "DestinationResolution",
    "DestinationType",
]
