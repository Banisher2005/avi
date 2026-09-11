"""Unified capability abstraction and result models for AVI Agent Runtime."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from avi.actions.base import BaseAction
from avi.safety.models import ActionCategory
from avi.tools.base import BaseTool, ToolResult


class DataClassification(str, Enum):
    """Privacy and data sharing boundaries for capability inputs and outputs."""

    LOCAL_ONLY = "LOCAL_ONLY"
    PROVIDER_ELIGIBLE = "PROVIDER_ELIGIBLE"
    USER_CONFIRMATION_REQUIRED = "USER_CONFIRMATION_REQUIRED"


class CapabilityCategory(str, Enum):
    """Unified categories for agent capabilities."""

    SYSTEM = "SYSTEM"
    APPLICATION = "APPLICATION"
    FILESYSTEM = "FILESYSTEM"
    BROWSER = "BROWSER"
    WEB = "WEB"
    COMMUNICATION = "COMMUNICATION"
    MEDIA = "MEDIA"
    PRODUCTIVITY = "PRODUCTIVITY"
    MEMORY = "MEMORY"
    DEVELOPMENT = "DEVELOPMENT"
    COMPUTER_USE = "COMPUTER_USE"


class ExecutionStatus(str, Enum):
    """Execution state outcome for single or multi-step capabilities."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


@dataclass
class CapabilityResult:
    """Standardized outcome returned by all capabilities."""

    success: bool
    status: ExecutionStatus
    data: Any = field(default_factory=dict)
    message: str = ""
    error: str | None = None
    classification: DataClassification = DataClassification.LOCAL_ONLY
    metadata: dict[str, Any] = field(default_factory=dict)

    def format_display(self) -> str:
        """Render readable text representation."""
        if self.message:
            return self.message
        if self.error:
            return f"Error: {self.error}"
        return str(self.data)

    def to_dict(self) -> dict[str, Any]:
        """Serialize CapabilityResult to dictionary."""
        return {
            "success": self.success,
            "status": self.status.value,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "classification": self.classification.value,
            "metadata": self.metadata,
        }


class BaseCapability(ABC):
    """Abstract base class for all agent capabilities."""

    name: str
    description: str
    category: CapabilityCategory = CapabilityCategory.SYSTEM
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    risk_category: ActionCategory = ActionCategory.READ_ONLY
    data_classification: DataClassification = DataClassification.LOCAL_ONLY
    requires_confirmation: bool = False
    side_effects: bool = False
    supports_observation: bool = True
    supports_rollback: bool = False
    tags: Sequence[str] = ()
    enabled: bool = True

    @property
    def parameters(self) -> dict[str, Any]:
        """Structured parameter specifications from input_schema."""
        return self.input_schema.get("properties", {})

    @property
    def risk_level(self) -> ActionCategory:
        """Risk level alias matching risk_category."""
        return self.risk_category

    @abstractmethod
    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute the capability and return a structured CapabilityResult."""
        pass

    def to_metadata(self) -> dict[str, Any]:
        """Export standardized metadata and JSON Schema for model reasoning."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value if hasattr(self.category, "value") else str(self.category),
            "input_schema": self.input_schema,
            "parameters": self.parameters,
            "risk_category": self.risk_category.value,
            "risk_level": self.risk_level.value,
            "data_classification": self.data_classification.value,
            "requires_confirmation": self.requires_confirmation,
            "side_effects": getattr(self, "side_effects", False),
            "supports_observation": getattr(self, "supports_observation", True),
            "supports_rollback": getattr(self, "supports_rollback", False),
            "tags": list(getattr(self, "tags", ())),
            "enabled": getattr(self, "enabled", True),
        }

    def to_compact_desc(self) -> dict[str, Any]:
        """Export a compact capability description for planning model prompt discovery."""
        return {
            "name": self.name,
            "purpose": self.description,
            "category": self.category.value if hasattr(self.category, "value") else str(self.category),
            "parameters": list(self.parameters.keys()),
            "risk_level": self.risk_level.value,
            "requires_confirmation": self.requires_confirmation,
        }


class ToolCapabilityAdapter(BaseCapability):
    """Adapts an existing read-only BaseTool to the unified BaseCapability interface."""

    def __init__(self, tool: BaseTool) -> None:
        self.tool = tool
        self.name = tool.name
        self.description = tool.description
        self.input_schema = getattr(tool, "input_schema", {"type": "object", "properties": {}})
        self.risk_category = ActionCategory.READ_ONLY
        self.data_classification = DataClassification.LOCAL_ONLY
        self.requires_confirmation = False

    def execute(self, **kwargs: Any) -> CapabilityResult:
        tool_res: ToolResult = self.tool.execute(**kwargs)
        status = ExecutionStatus.SUCCESS if tool_res.success else ExecutionStatus.FAILED
        return CapabilityResult(
            success=tool_res.success,
            status=status,
            data=tool_res.data,
            message=tool_res.format_display(),
            error=tool_res.error,
            classification=self.data_classification,
        )


class ActionCapabilityAdapter(BaseCapability):
    """Adapts an existing BaseAction instance or class to the BaseCapability interface."""

    def __init__(
        self,
        action: BaseAction,
        name: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.action = action
        self.name = name or getattr(action, "name", "assistant.action")
        self.description = description or f"Execute action {self.name}"
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.risk_category = getattr(action, "category", ActionCategory.LOW_RISK_ACTION)
        self.requires_confirmation = getattr(action, "requires_confirmation", False)
        self.data_classification = DataClassification.LOCAL_ONLY

    def execute(self, **kwargs: Any) -> CapabilityResult:
        act_res = self.action.execute()
        status = ExecutionStatus.SUCCESS if act_res.success else ExecutionStatus.FAILED
        return CapabilityResult(
            success=act_res.success,
            status=status,
            data=act_res.data,
            message=act_res.message,
            error=act_res.message if not act_res.success else None,
            classification=self.data_classification,
        )
