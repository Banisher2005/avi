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
from avi.safety.models import ActionCategory, PolicyDecision

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "error": self.error,
            "sanitized_arguments": self.sanitized_arguments,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_reason": self.confirmation_reason,
            "risk_level": self.risk_level,
        }


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
        if isinstance(tool_call, dict):
            name = tool_call.get("name") or tool_call.get("tool") or ""
            args = tool_call.get("arguments") or tool_call.get("args") or {}
        elif isinstance(tool_call, ToolCall):
            name = tool_call.name
            args = tool_call.arguments
        else:
            return ValidationResult(
                valid=False,
                error="Invalid tool call structure: must be ToolCall or dict.",
            )

        name = str(name).strip()
        if not name:
            return ValidationResult(
                valid=False,
                error="Empty tool name provided.",
            )

        if not isinstance(args, dict):
            return ValidationResult(
                valid=False,
                error=f"Malformed arguments for tool '{name}': expected dictionary, got {type(args).__name__}.",
            )

        # 2. Check capability existence
        cap = self.registry.get(name)
        if not cap:
            return ValidationResult(
                valid=False,
                error=f"Unknown tool or capability: '{name}'. Hallucinated capabilities are rejected.",
            )

        contract = self.registry.get_tool_contract(cap.name)
        if not contract:
            return ValidationResult(
                valid=False,
                error=f"Capability '{name}' has no valid tool contract.",
            )

        sanitized_args = dict(args)

        # 3. Check required parameters
        for req_param in contract.required_parameters:
            if req_param not in sanitized_args or sanitized_args[req_param] is None:
                return ValidationResult(
                    valid=False,
                    contract=contract,
                    error=f"Missing required parameter '{req_param}' for capability '{name}'.",
                )
            # Empty strings for required parameters are rejected unless explicitly allowed
            if isinstance(sanitized_args[req_param], str) and not sanitized_args[req_param].strip():
                return ValidationResult(
                    valid=False,
                    contract=contract,
                    error=f"Required parameter '{req_param}' cannot be empty.",
                )

        # 4. Type validation
        for param_name, val in list(sanitized_args.items()):
            expected_type = contract.parameter_types.get(param_name, "string")
            if not self._check_type(val, expected_type):
                return ValidationResult(
                    valid=False,
                    contract=contract,
                    error=(
                        f"Invalid type for parameter '{param_name}' in tool '{name}': "
                        f"expected {expected_type}, got {type(val).__name__}."
                    ),
                )

        # 5. Path safety checks
        path_keys = {"path", "destination", "source", "file", "target", "folder", "directory"}
        for k, val in sanitized_args.items():
            if (k.lower() in path_keys or "path" in k.lower()) and isinstance(val, str):
                path_err = self._validate_path_safety(val, cap_name=name)
                if path_err:
                    return ValidationResult(
                        valid=False,
                        contract=contract,
                        error=path_err,
                    )

        # 6. Terminal / Command execution safety
        if name in ("system.execute_command", "guarded_terminal", "desktop.open_terminal"):
            cmd = sanitized_args.get("command") or sanitized_args.get("cmd") or ""
            cmd_err = self._validate_command_safety(str(cmd))
            if cmd_err:
                return ValidationResult(
                    valid=False,
                    contract=contract,
                    error=cmd_err,
                )

        # 7. SafetyEngine Policy Check
        risk_level = contract.risk_level
        requires_conf = contract.confirmation_requirement
        conf_reason = None

        if self.safety_engine:
            policy_target = (
                sanitized_args.get("path")
                or sanitized_args.get("destination")
                or sanitized_args.get("target")
                or sanitized_args.get("command")
                or ""
            )
            decision = self.safety_engine.evaluate(
                action=cap.name,
                category=getattr(cap, "risk_category", ActionCategory.LOW_RISK_ACTION),
                target=str(policy_target),
                arguments=sanitized_args,
                confirmed=confirmed,
            )

            if decision.decision == PolicyDecision.BLOCKED:
                return ValidationResult(
                    valid=False,
                    contract=contract,
                    error=f"Action '{name}' blocked by safety policy: {decision.reason}",
                    risk_level=risk_level,
                )

            if decision.decision == PolicyDecision.REQUIRES_CONFIRMATION:
                requires_conf = True
                conf_reason = decision.reason or f"Action '{name}' requires confirmation."

        return ValidationResult(
            valid=True,
            sanitized_arguments=sanitized_args,
            contract=contract,
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
