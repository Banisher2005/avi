"""Application discovery and resolution package for AVI."""

from avi.apps.models import ApplicationResolution
from avi.apps.resolver import ApplicationResolver

__all__ = ["ApplicationResolution", "ApplicationResolver"]
