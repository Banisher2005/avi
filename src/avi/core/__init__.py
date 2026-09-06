"""Core processing and routing subsystems for AVI."""

from avi.core.normalizer import normalize_response, normalize_stream
from avi.core.router import Router

__all__ = ["Router", "normalize_response", "normalize_stream"]
