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
class ToolContract:
    """Machine-readable tool contract for dynamic agent planning and LLM tool calling."""

    name: str
    description: str
    category: str
    parameters: dict[str, Any] = field(default_factory=dict)
    parameter_types: dict[str, str] = field(default_factory=dict)
    required_parameters: list[str] = field(default_factory=list)
    optional_parameters: list[str] = field(default_factory=list)
    side_effects: bool = False
    risk_level: str = "READ_ONLY"
    confirmation_requirement: bool = False
    expected_result: str = "object"
    observable_outputs: list[str] = field(default_factory=list)
    verification_method: str = "state_inspection"

    def to_dict(self) -> dict[str, Any]:
        """Serialize ToolContract to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": self.parameters,
            "parameter_types": self.parameter_types,
            "required_parameters": self.required_parameters,
            "optional_parameters": self.optional_parameters,
            "side_effects": self.side_effects,
            "risk_level": self.risk_level,
            "confirmation_requirement": self.confirmation_requirement,
            "expected_result": self.expected_result,
            "observable_outputs": self.observable_outputs,
            "verification_method": self.verification_method,
        }


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
    summary: str = ""
    observations: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    retryable: bool = True

    def __post_init__(self) -> None:
        """Populate default summary and artifacts if not provided."""
        if not self.summary:
            self.summary = self.message or ("Success" if self.success else (self.error or "Failed"))
        if not self.artifacts and isinstance(self.data, dict):
            for k in ("path", "destination", "file", "url", "saved_path", "output_path"):
                val = self.data.get(k)
                if val and isinstance(val, str) and val not in self.artifacts:
                    self.artifacts.append(val)

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
            "summary": self.summary,
            "observations": self.observations,
            "artifacts": self.artifacts,
            "retryable": self.retryable,
        }


# Backwards compatibility alias for dynamic tool result
AgentToolResult = CapabilityResult


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

    def to_tool_contract(self) -> ToolContract:
        """Export standardized machine-readable tool contract for dynamic agent planning."""
        props = self.input_schema.get("properties", {}) if isinstance(self.input_schema, dict) else {}
        required = list(self.input_schema.get("required", [])) if isinstance(self.input_schema, dict) else []
        param_types = {k: v.get("type", "string") if isinstance(v, dict) else "string" for k, v in props.items()}
        opt_params = [k for k in props if k not in required]
        cat_str = self.category.value if hasattr(self.category, "value") else str(self.category)
        risk_str = self.risk_level.value if hasattr(self.risk_level, "value") else str(self.risk_level)

        return ToolContract(
            name=self.name,
            description=self.description,
            category=cat_str,
            parameters=props,
            parameter_types=param_types,
            required_parameters=required,
            optional_parameters=opt_params,
            side_effects=getattr(self, "side_effects", False),
            risk_level=risk_str,
            confirmation_requirement=getattr(self, "requires_confirmation", False),
            expected_result=getattr(self, "expected_result", "object"),
            observable_outputs=list(getattr(self, "observable_outputs", ())),
            verification_method=getattr(self, "verification_method", "state_inspection"),
        )


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
