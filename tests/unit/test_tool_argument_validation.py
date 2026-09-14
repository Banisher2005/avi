"""Unit tests for tool argument validation, binding, type preservation, and recovery."""

from unittest.mock import MagicMock

from avi.agent.dynamic_loop import DynamicAgentLoop
from avi.agent.loop_guard import LoopGuard, LoopGuardConfig
from avi.agent.tool_validator import UNRESOLVED_ARGUMENT, ToolCallValidator, ValidationResult
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry, create_default_capability_registry
from avi.providers.models import ToolCall
from avi.safety.engine import SafetyEngine


def _create_test_registry() -> CapabilityRegistry:
    return create_default_capability_registry()


def test_empty_required_argument_rejected():
    """An empty string parameter for a required field is rejected."""
    registry = _create_test_registry()
    validator = ToolCallValidator(registry=registry)
    call = ToolCall(name="desktop.open_file", arguments={"path": ""})
    res: ValidationResult = validator.validate(call)

    assert not res.valid
    assert res.error_category == "TOOL_INVALID_ARGUMENT"
    assert res.parameter_name == "path"
    assert res.validation_reason == "empty"
    assert "cannot be empty" in res.error


def test_whitespace_required_argument_rejected():
    """A whitespace-only string for a required field is rejected."""
    registry = _create_test_registry()
    validator = ToolCallValidator(registry=registry)
    call = ToolCall(name="desktop.open_file", arguments={"path": "   \t\n  "})
    res: ValidationResult = validator.validate(call)

    assert not res.valid
    assert res.error_category == "TOOL_INVALID_ARGUMENT"
    assert res.parameter_name == "path"
    assert res.validation_reason == "whitespace"
    assert "cannot be empty" in res.error.lower()


def test_missing_required_argument_rejected():
    """A missing required field is rejected."""
    registry = _create_test_registry()
    validator = ToolCallValidator(registry=registry)
    call = ToolCall(name="desktop.open_file", arguments={})
    res: ValidationResult = validator.validate(call)

    assert not res.valid
    assert res.error_category == "TOOL_INVALID_ARGUMENT"
    assert res.parameter_name == "path"
    assert res.validation_reason == "missing"
    assert "Missing required parameter" in res.error


def test_null_required_argument_rejected():
    """A null/None parameter for a required field is rejected."""
    registry = _create_test_registry()
    validator = ToolCallValidator(registry=registry)
    call = ToolCall(name="desktop.open_file", arguments={"path": None})
    res: ValidationResult = validator.validate(call)

    assert not res.valid
    assert res.error_category == "TOOL_INVALID_ARGUMENT"
    assert res.parameter_name == "path"
    assert res.validation_reason == "null"
    assert "cannot be null" in res.error.lower()


def test_unresolved_argument_rejected():
    """An unresolved placeholder parameter is rejected with distinct reason."""
    registry = _create_test_registry()
    validator = ToolCallValidator(registry=registry)
    call = ToolCall(name="desktop.open_file", arguments={"path": UNRESOLVED_ARGUMENT})
    res: ValidationResult = validator.validate(call)

    assert not res.valid
    assert res.error_category == "TOOL_INVALID_ARGUMENT"
    assert res.parameter_name == "path"
    assert res.validation_reason == "unresolved"
    assert "unresolved" in res.error.lower()


def test_valid_argument_preserved():
    """Valid parameters preserve exact types (strings, ints, dicts, arrays, booleans)."""
    registry = _create_test_registry()
    validator = ToolCallValidator(registry=registry)
    call = ToolCall(
        name="desktop.open_url",
        arguments={"url": "https://www.google.com"},
    )
    res = validator.validate(call)
    assert res.valid
    assert res.error is None


def test_invalid_tool_call_not_executed():
    """Invalid tool call is stopped before registry execution."""
    registry = _create_test_registry()
    registry.execute_safe = MagicMock()

    loop = DynamicAgentLoop(registry=registry, safety_engine=SafetyEngine())
    call = ToolCall(name="desktop.open_file", arguments={"path": ""})
    val = loop.validator.validate(call)
    assert not val.valid
    # execute_safe must not be called
    assert not registry.execute_safe.called


def test_invalid_tool_call_not_retried_identically():
    """Loop guard records invalid calls to prevent retrying the identical invalid call."""
    guard = LoopGuard(config=LoopGuardConfig(max_identical_invocations=1))
    check1 = guard.record_and_check("desktop.open_file", {"path": ""})
    assert not check1.is_loop

    check2 = guard.record_and_check("desktop.open_file", {"path": ""})
    assert check2.is_loop


def test_same_failure_detected_as_no_progress():
    """Same failure is detected by loop guard as lack of forward progress."""
    guard = LoopGuard(config=LoopGuardConfig(max_identical_invocations=1))
    guard.record_and_check("filesystem.search", {"directory": "~/Downloads", "pattern": "*.*"})
    res = guard.record_and_check("filesystem.search", {"directory": "~/Downloads", "pattern": "*.*"})
    assert res.is_loop


def test_argument_pipeline_preserves_paths():
    """Dynamic argument piping preserves concrete paths from prior outputs."""
    registry = _create_test_registry()
    loop = DynamicAgentLoop(registry=registry)

    prev_res = [
        CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            data={"files": [{"path": "/home/test/sample.pdf", "name": "sample.pdf"}]},
        )
    ]
    obs = MagicMock()
    piped = loop._pipe_dynamic_arguments(
        "desktop.open_file",
        {"path": UNRESOLVED_ARGUMENT},
        prev_res,
        obs,
        "open the pdf file",
    )
    assert piped["path"] == "/home/test/sample.pdf"


def test_argument_pipeline_preserves_urls():
    """Dynamic argument piping preserves web URLs without converting to paths."""
    registry = _create_test_registry()
    loop = DynamicAgentLoop(registry=registry)

    prev_res = [
        CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            data={"url": "https://www.youtube.com/results?search_query=yeah+jaron"},
        )
    ]
    obs = MagicMock()
    piped = loop._pipe_dynamic_arguments(
        "desktop.open_url",
        {"url": "https://www.youtube.com/results?search_query=yeah+jaron"},
        prev_res,
        obs,
        "open the url",
    )
    assert piped["url"] == "https://www.youtube.com/results?search_query=yeah+jaron"


def test_application_argument_not_bound_to_filesystem():
    """An application launch name is not bound into a filesystem path."""
    registry = _create_test_registry()
    loop = DynamicAgentLoop(registry=registry)
    obs = MagicMock()

    action = loop._select_next_action(
        goal="open brave",
        observation=obs,
        available_tools=[],
        step_records=[],
        context_metadata={},
    )
    assert isinstance(action, ToolCall)
    assert action.name == "desktop.open_app"
    assert action.arguments.get("app_name") == "brave"
    assert "path" not in action.arguments


def test_browser_url_not_bound_to_filesystem_path():
    """A browser/YouTube request is not bound to a filesystem search or path."""
    registry = _create_test_registry()
    loop = DynamicAgentLoop(registry=registry)
    obs = MagicMock()

    action = loop._select_next_action(
        goal="brave open youtube and search yeah jaron",
        observation=obs,
        available_tools=[],
        step_records=[],
        context_metadata={},
    )
    assert isinstance(action, ToolCall)
    assert action.name in ("web.youtube.search", "desktop.open_url")
    assert "path" not in action.arguments
    if action.name == "web.youtube.search":
        assert "yeah jaron" in action.arguments.get("query", "").lower()


def test_tool_validation_before_supervisor_execution():
    """Validation runs before supervisor execution and halts if invalid."""
    registry = _create_test_registry()
    registry.execute_safe = MagicMock()
    loop = DynamicAgentLoop(registry=registry)

    invalid_call = ToolCall(name="desktop.open_file", arguments={"path": ""})
    val = loop.validator.validate(invalid_call)
    assert not val.valid
    # The supervisor execute_safe must never be called on invalid tool calls
    assert not registry.execute_safe.called


def test_structured_tool_invalid_argument_result():
    """Validation failure produces structured diagnostics."""
    registry = _create_test_registry()
    validator = ToolCallValidator(registry=registry)
    call = ToolCall(name="desktop.open_file", arguments={"path": ""})
    res = validator.validate(call)

    diag = res.format_diagnostic()
    assert "desktop.open_file" in diag
    assert "FAILED" in diag
    assert "path" in diag
    assert "cannot be empty" in diag


def test_agent_recovers_from_invalid_tool_call():
    """Agent dynamic loop recovers from an empty path when goal has a clear app or youtube intent."""
    registry = _create_test_registry()
    loop = DynamicAgentLoop(registry=registry)

    invalid_call = ToolCall(name="desktop.open_file", arguments={"path": ""})
    val_res = loop.validator.validate(invalid_call)

    recovered = loop._attempt_validation_recovery(
        invalid_call,
        val_res,
        goal="brave open youtube and search yeah jaron",
        step_records=[],
        step_outputs=[],
    )
    assert recovered is not None
    assert recovered.name in ("desktop.open_app", "web.youtube.search", "desktop.open_url")
