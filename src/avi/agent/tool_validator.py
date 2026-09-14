"""Validation engine for tool calls prior to agent execution."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avi.capabilities.models import ToolContract
from avi.capabilities.registry import CapabilityRegistry
from avi.providers.models import ToolCall
from avi.safety.engine import SafetyEngine
from avi.safety.models import SafetyAssessment

logger = logging.getLogger("avi.agent.tool_validator")

# Critical protected system paths that should never be modified or targeted dangerously
_CRITICAL_SYSTEM_PATHS = {
    "/etc",
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
    "/boot",
    "/root",
    "/sys",
    "/proc",
    "/dev",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/var",
}

_DESTRUCTIVE_COMMAND_PATTERNS = [
    r"\brm\s+-[rR]?[fF]?\s+(?:/\s*$|/\*|~/\*)",
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=/dev/",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r">\s*/dev/sd[a-z]",
]


UNRESOLVED_ARGUMENT = "<UNRESOLVED_ARGUMENT>"


@dataclass
class ValidationResult:
    """Outcome of validating a requested ToolCall against capability contract and safety rules."""

    valid: bool
    error: str | None = None
    sanitized_arguments: dict[str, Any] = field(default_factory=dict)
    contract: ToolContract | None = None
    requires_confirmation: bool = False
    confirmation_reason: str | None = None
    risk_level: str = "READ_ONLY"
    error_category: str = "TOOL_INVALID_ARGUMENT"
    tool_name: str | None = None
    parameter_name: str | None = None
    validation_reason: str | None = None  # "missing", "null", "empty", "whitespace", "unresolved", "invalid_type", "path_safety", "command_safety", "policy_blocked"
    recoverable: bool = True
    task_id: str | None = None
    operation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "error": self.error,
            "sanitized_arguments": self.sanitized_arguments,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_reason": self.confirmation_reason,
            "risk_level": self.risk_level,
            "error_category": self.error_category,
            "tool_name": self.tool_name,
            "parameter_name": self.parameter_name,
            "validation_reason": self.validation_reason,
            "recoverable": self.recoverable,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
        }

    def format_diagnostic(self) -> str:
        """Render clean multi-line diagnostic block for logging and UI debugging."""
        status = "PASSED" if self.valid else "FAILED"
        lines = [
            f"TOOL CALL\n{self.tool_name or 'unknown'}",
            f"VALIDATION\n{status}",
        ]
        if not self.valid:
            if self.parameter_name:
                lines.append(f"PARAMETER\n{self.parameter_name}")
            if self.validation_reason:
                reason_desc = {
                    "missing": "required parameter missing",
                    "null": "required parameter is null",
                    "empty": "required value empty",
                    "whitespace": "required value whitespace",
                    "unresolved": "unresolved parameter reference",
                    "invalid_type": "parameter type mismatch",
                    "path_safety": "path traversal or protected path",
                    "command_safety": "destructive command blocked",
                    "policy_blocked": "safety policy blocked",
                }.get(self.validation_reason, self.validation_reason)
                lines.append(f"REASON\n{reason_desc}")
            if self.error:
                lines.append(f"MESSAGE\n{self.error}")
        return "\n".join(lines)


class ToolCallValidator:
    """Validates and sanitizes model-generated tool calls before dispatch."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        safety_engine: SafetyEngine | None = None,
    ) -> None:
        self.registry = registry
        self.safety_engine = safety_engine or SafetyEngine()

    def validate(
        self,
        tool_call: ToolCall | dict[str, Any],
        confirmed: bool = False,
    ) -> ValidationResult:
        """Validate a tool call against registered contracts, schemas, path safety, and policy."""
        # 1. Normalize input
        task_id = None
        operation_id = None
        if isinstance(tool_call, dict):
            name = tool_call.get("name") or tool_call.get("tool") or tool_call.get("capability") or ""
            args = tool_call.get("arguments") or tool_call.get("args") or {}
            task_id = tool_call.get("task_id")
            operation_id = tool_call.get("operation_id") or tool_call.get("id") or tool_call.get("call_id")
        elif isinstance(tool_call, ToolCall):
            name = tool_call.name
            args = tool_call.arguments
            task_id = getattr(tool_call, "task_id", None)
            operation_id = getattr(tool_call, "operation_id", None) or getattr(tool_call, "id", None) or tool_call.call_id
        else:
            res = ValidationResult(
                valid=False,
                error="Invalid tool call structure: must be ToolCall or dict.",
                error_category="TOOL_INVALID_ARGUMENT",
                validation_reason="invalid_structure",
                recoverable=False,
            )
            logger.debug("Tool validation failed: %s", res.error)
            return res

        name = str(name).strip()
        if not name:
            res = ValidationResult(
                valid=False,
                error="Empty tool name provided.",
                error_category="TOOL_INVALID_ARGUMENT",
                validation_reason="empty_name",
                recoverable=False,
            )
            logger.debug("Tool validation failed: %s", res.error)
            return res

        if not isinstance(args, dict):
            res = ValidationResult(
                valid=False,
                tool_name=name,
                task_id=task_id,
                operation_id=operation_id,
                error=f"Malformed arguments for tool '{name}': expected dictionary, got {type(args).__name__}.",
                error_category="TOOL_INVALID_ARGUMENT",
                validation_reason="malformed_arguments",
                recoverable=False,
            )
            logger.debug("Tool validation failed: %s", res.error)
            return res

        # 2. Check capability existence
        cap = self.registry.get(name)
        if not cap:
            res = ValidationResult(
                valid=False,
                tool_name=name,
                task_id=task_id,
                operation_id=operation_id,
                error=f"Unknown tool or capability: '{name}'. Hallucinated capabilities are rejected.",
                error_category="UNKNOWN_CAPABILITY",
                validation_reason="unknown_capability",
                recoverable=True,
            )
            logger.debug("Tool validation failed: %s", res.error)
            return res

        contract = self.registry.get_tool_contract(cap.name)
        if not contract:
            res = ValidationResult(
                valid=False,
                tool_name=name,
                task_id=task_id,
                operation_id=operation_id,
                error=f"Capability '{name}' has no valid tool contract.",
                error_category="TOOL_UNAVAILABLE",
                validation_reason="no_contract",
                recoverable=False,
            )
            logger.debug("Tool validation failed: %s", res.error)
            return res

        sanitized_args = dict(args)

        # 3. Check required parameters (distinguish missing, null, empty, whitespace, unresolved)
        for req_param in contract.required_parameters:
            if req_param not in sanitized_args:
                res = ValidationResult(
                    valid=False,
                    contract=contract,
                    tool_name=name,
                    task_id=task_id,
                    operation_id=operation_id,
                    parameter_name=req_param,
                    validation_reason="missing",
                    error_category="TOOL_INVALID_ARGUMENT",
                    error=f"Missing required parameter '{req_param}' for capability '{name}'.",
                    recoverable=True,
                )
                logger.debug("Tool validation rejected missing required argument:\n%s", res.format_diagnostic())
                return res

            val = sanitized_args[req_param]

            if val is None:
                res = ValidationResult(
                    valid=False,
                    contract=contract,
                    tool_name=name,
                    task_id=task_id,
                    operation_id=operation_id,
                    parameter_name=req_param,
                    validation_reason="null",
                    error_category="TOOL_INVALID_ARGUMENT",
                    error=f"Required parameter '{req_param}' cannot be null for capability '{name}'.",
                    recoverable=True,
                )
                logger.debug("Tool validation rejected null required argument:\n%s", res.format_diagnostic())
                return res

            if val == UNRESOLVED_ARGUMENT or val == "<UNRESOLVED_ARGUMENT>":
                res = ValidationResult(
                    valid=False,
                    contract=contract,
                    tool_name=name,
                    task_id=task_id,
                    operation_id=operation_id,
                    parameter_name=req_param,
                    validation_reason="unresolved",
                    error_category="TOOL_INVALID_ARGUMENT",
                    error=f"Required parameter '{req_param}' is unresolved for capability '{name}'.",
                    recoverable=True,
                )
                logger.debug("Tool validation rejected unresolved argument:\n%s", res.format_diagnostic())
                return res

            if isinstance(val, str):
                trimmed = val.strip()
                if not trimmed:
                    reason = "whitespace" if val else "empty"
                    res = ValidationResult(
                        valid=False,
                        contract=contract,
                        tool_name=name,
                        task_id=task_id,
                        operation_id=operation_id,
                        parameter_name=req_param,
                        validation_reason=reason,
                        error_category="TOOL_INVALID_ARGUMENT",
                        error=f"Required parameter '{req_param}' cannot be empty for capability '{name}'.",
                        recoverable=True,
                    )
                    logger.debug("Tool validation rejected empty/whitespace required argument:\n%s", res.format_diagnostic())
                    return res
                # Normalize whitespace
                sanitized_args[req_param] = trimmed

        # 4. Type validation
        for param_name, val in list(sanitized_args.items()):
            if param_name in contract.parameters:
                expected_type = contract.parameter_types.get(param_name, "string")
                if not self._check_type(val, expected_type):
                    res = ValidationResult(
                        valid=False,
                        contract=contract,
                        tool_name=name,
                        task_id=task_id,
                        operation_id=operation_id,
                        parameter_name=param_name,
                        validation_reason="invalid_type",
                        error_category="TOOL_INVALID_ARGUMENT",
                        error=(
                            f"Invalid type for parameter '{param_name}' in tool '{name}': "
                            f"expected {expected_type}, got {type(val).__name__}."
                        ),
                        recoverable=True,
                    )
                    logger.debug("Tool validation rejected type mismatch:\n%s", res.format_diagnostic())
                    return res

        # 5. Path safety checks
        path_keys = {"path", "destination", "source", "file", "target", "folder", "directory"}
        for k, val in sanitized_args.items():
            if (k.lower() in path_keys or "path" in k.lower()) and isinstance(val, str):
                path_err = self._validate_path_safety(val, cap_name=name)
                if path_err:
                    res = ValidationResult(
                        valid=False,
                        contract=contract,
                        tool_name=name,
                        task_id=task_id,
                        operation_id=operation_id,
                        parameter_name=k,
                        validation_reason="path_safety",
                        error_category="TOOL_INVALID_ARGUMENT",
                        error=path_err,
                        recoverable=False,
                    )
                    logger.debug("Tool validation rejected unsafe path:\n%s", res.format_diagnostic())
                    return res

        # 6. Terminal / Command execution safety
        if name in ("system.execute_command", "guarded_terminal", "desktop.open_terminal") or "command" in sanitized_args:
            cmd = sanitized_args.get("command") or sanitized_args.get("cmd") or ""
            cmd_err = self._validate_command_safety(str(cmd))
            if cmd_err:
                res = ValidationResult(
                    valid=False,
                    contract=contract,
                    tool_name=name,
                    task_id=task_id,
                    operation_id=operation_id,
                    parameter_name="command",
                    validation_reason="command_safety",
                    error_category="TOOL_INVALID_ARGUMENT",
                    error=cmd_err,
                    recoverable=False,
                )
                logger.debug("Tool validation rejected unsafe command:\n%s", res.format_diagnostic())
                return res

        # 7. SafetyEngine Policy Check
        risk_level = contract.risk_level
        requires_conf = contract.confirmation_requirement
        conf_reason = None

        if self.safety_engine and (name in ("system.execute_command", "guarded_terminal", "desktop.open_terminal") or "command" in sanitized_args):
            cmd_target = str(sanitized_args.get("command") or sanitized_args.get("cmd") or "")
            if cmd_target:
                assessment: SafetyAssessment = self.safety_engine.evaluate(cmd_target)

                if assessment.is_blocked:
                    res = ValidationResult(
                        valid=False,
                        contract=contract,
                        tool_name=name,
                        task_id=task_id,
                        operation_id=operation_id,
                        error=f"Action '{name}' blocked by safety policy: {assessment.reason}",
                        error_category="RESOURCE_CONFLICT",
                        validation_reason="policy_blocked",
                        risk_level=risk_level,
                        recoverable=False,
                    )
                    logger.debug("Tool validation rejected by safety policy:\n%s", res.format_diagnostic())
                    return res

                if assessment.requires_confirmation:
                    requires_conf = True
                    conf_reason = assessment.reason or f"Action '{name}' requires confirmation."

        if requires_conf and not conf_reason:
            conf_reason = f"Action '{name}' requires confirmation before proceeding."

        return ValidationResult(
            valid=True,
            sanitized_arguments=sanitized_args,
            contract=contract,
            tool_name=name,
            task_id=task_id,
            operation_id=operation_id,
            requires_confirmation=requires_conf,
            confirmation_reason=conf_reason,
            risk_level=risk_level,
        )

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Validate value conforms to expected JSON schema primitive type."""
        t = expected_type.lower()
        if t in ("string", "str"):
            return isinstance(value, str)
        if t in ("integer", "int"):
            return isinstance(value, int) and not isinstance(value, bool)
        if t in ("number", "float"):
            return (isinstance(value, (int, float))) and not isinstance(value, bool)
        if t in ("boolean", "bool"):
            return isinstance(value, bool)
        if t in ("array", "list"):
            return isinstance(value, (list, tuple))
        if t in ("object", "dict"):
            return isinstance(value, dict)
        return True

    def _validate_path_safety(self, path_str: str, cap_name: str = "") -> str | None:
        """Check for directory traversal and targeting critical system paths."""
        if "\x00" in path_str:
            return f"Path contains null bytes: '{path_str}'."

        cleaned = path_str.strip()
        if not cleaned:
            return None

        # Disallow raw root targeting for destructive operations
        if cleaned == "/" and any(term in cap_name for term in ("delete", "move", "write", "organize")):
            return "Destructive operations on filesystem root '/' are strictly forbidden."

        try:
            expanded = Path(cleaned).expanduser()
            resolved = expanded.resolve()
            res_str = str(resolved)

            # Check critical system paths
            if any(term in cap_name for term in ("delete", "move", "write", "organize")):
                for sys_p in _CRITICAL_SYSTEM_PATHS:
                    if res_str == sys_p or res_str.startswith(f"{sys_p}/"):
                        # User home inside /home or similar is permitted
                        if res_str.startswith(str(Path.home().resolve())):
                            continue
                        return f"Operation on protected system path '{res_str}' is forbidden."
        except Exception as exc:
            return f"Invalid or unresolvable path '{path_str}': {exc}"

        return None

    def _validate_command_safety(self, command_str: str) -> str | None:
        """Detect catastrophically destructive terminal commands."""
        clean = command_str.strip()
        for pat in _DESTRUCTIVE_COMMAND_PATTERNS:
            if re.search(pat, clean):
                return f"Potentially destructive or malicious command detected: '{command_str}'."
        return None
