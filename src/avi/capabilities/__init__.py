"""AVI Capability system providing unified desktop, filesystem, and system actions."""

from avi.capabilities.models import (
    ActionCapabilityAdapter,
    BaseCapability,
    CapabilityCategory,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
    ToolCapabilityAdapter,
)
from avi.capabilities.registry import CapabilityRegistry, create_default_capability_registry

__all__ = [
    "ActionCapabilityAdapter",
    "BaseCapability",
    "CapabilityCategory",
    "CapabilityResult",
    "CapabilityRegistry",
    "DataClassification",
    "ExecutionStatus",
    "ToolCapabilityAdapter",
    "create_default_capability_registry",
]
