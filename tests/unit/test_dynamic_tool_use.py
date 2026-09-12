from pathlib import Path

import pytest

from avi.agent.observation import FilesystemObservation, ObservationManager, UnifiedObservation
from avi.agent.tool_validator import ToolCallValidator
from avi.capabilities import create_default_capability_registry
from avi.capabilities.models import CapabilityResult, ExecutionStatus, ToolContract
from avi.providers.models import ToolCall


class TestToolContract:
    """Tests for machine-readable ToolContract metadata."""

    def test_tool_contract_generation_from_registry(self) -> None:
        registry = create_default_capability_registry()
        contract = registry.get_tool_contract("filesystem.create_directory")

        assert contract is not None
        assert isinstance(contract, ToolContract)
        assert contract.name == "filesystem.create_directory"
        assert contract.category == "FILESYSTEM"
        assert "path" in contract.required_parameters
        assert contract.parameter_types.get("path") == "string"
        assert not contract.confirmation_requirement

    def test_tool_contract_serialization(self) -> None:
        contract = ToolContract(
            name="test.action",
            description="Test capability",
            category="SYSTEM",
            parameters={"param1": {"type": "string"}},
            parameter_types={"param1": "string"},
            required_parameters=["param1"],
            optional_parameters=[],
            side_effects=True,
            risk_level="HIGH_RISK_ACTION",
            confirmation_requirement=True,
            expected_result="dict",
            observable_outputs=["path"],
            verification_method="file_exists",
        )
        d = contract.to_dict()
        assert d["name"] == "test.action"
        assert d["side_effects"] is True
        assert d["confirmation_requirement"] is True
        assert d["required_parameters"] == ["param1"]

    def test_registry_get_tool_contracts_list(self) -> None:
        registry = create_default_capability_registry()
        contracts = registry.get_tool_contracts(enabled_only=True)
        assert len(contracts) > 10
        names = [c.name for c in contracts]
        assert "filesystem.search" in names
        assert "desktop.screenshot" in names
        assert "filesystem.rename" in names


class TestToolCallValidator:
    """Tests for ToolCallValidator security, schema, and policy enforcement."""

    @pytest.fixture
    def validator(self) -> ToolCallValidator:
        registry = create_default_capability_registry()
        return ToolCallValidator(registry=registry)

    def test_valid_tool_call(self, validator: ToolCallValidator) -> None:
        tc = ToolCall(name="filesystem.search", arguments={"query": "*.pdf", "path": "~/Downloads"})
        result = validator.validate(tc)
        assert result.valid is True
        assert result.error is None
        assert result.sanitized_arguments["query"] == "*.pdf"

    def test_reject_hallucinated_tool(self, validator: ToolCallValidator) -> None:
        tc = ToolCall(name="filesystem.delete_everything", arguments={"path": "/"})
        result = validator.validate(tc)
        assert result.valid is False
        assert "Unknown tool or capability" in str(result.error)

    def test_reject_missing_required_argument(self, validator: ToolCallValidator) -> None:
        tc = ToolCall(name="filesystem.create_directory", arguments={})
        result = validator.validate(tc)
        assert result.valid is False
        assert "Missing required parameter 'path'" in str(result.error)

    def test_reject_invalid_argument_type(self, validator: ToolCallValidator) -> None:
        tc = ToolCall(name="filesystem.create_directory", arguments={"path": {"bad": "object"}})
        result = validator.validate(tc)
        assert result.valid is False
        assert "Invalid type for parameter 'path'" in str(result.error)

    def test_reject_null_byte_path(self, validator: ToolCallValidator) -> None:
        tc = ToolCall(name="filesystem.read_file", arguments={"path": "/home/user/\x00secret.txt"})
        result = validator.validate(tc)
        assert result.valid is False
        assert "null bytes" in str(result.error)

    def test_reject_destructive_root_deletion(self, validator: ToolCallValidator) -> None:
        tc = ToolCall(name="filesystem.delete", arguments={"path": "/"})
        result = validator.validate(tc)
        assert result.valid is False
        assert "strictly forbidden" in str(result.error)

    def test_reject_destructive_terminal_command(self, validator: ToolCallValidator) -> None:
        tc = ToolCall(name="system.execute_command", arguments={"command": "rm -rf /"})
        result = validator.validate(tc)
        assert result.valid is False
        assert "destructive" in str(result.error).lower()


class TestObservationManager:
    """Tests for ObservationManager and bounded UnifiedObservation."""

    def test_observe_filesystem_existing_file(self, tmp_path: Path) -> None:
        obs_mgr = ObservationManager()
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir(parents=True, exist_ok=True)
        test_file = obs_dir / "sample.txt"
        test_file.write_text("Hello AVI")

        fs_obs = obs_mgr.observe_filesystem(str(test_file))
        assert fs_obs.exists is True
        assert fs_obs.is_file is True
        assert fs_obs.is_dir is False
        assert fs_obs.size_bytes == len("Hello AVI")
        assert fs_obs.modified_at != ""

    def test_observe_filesystem_nonexistent(self) -> None:
        obs_mgr = ObservationManager()
        fs_obs = obs_mgr.observe_filesystem("/nonexistent/path/to/nothing_xyz.pdf")
        assert fs_obs.exists is False

    def test_observe_browser_bounded(self) -> None:
        obs_mgr = ObservationManager()
        long_text = "A" * 1000
        br_obs = obs_mgr.observe_browser(url="https://example.com", title="Example", text_snippet=long_text)
        assert br_obs.url == "https://example.com"
        assert len(br_obs.visible_text_summary) <= 500

    def test_unified_observation_formatting(self) -> None:
        fs = FilesystemObservation(path="/tmp/test.pdf", exists=True, is_file=True, size_bytes=1024)
        unified = UnifiedObservation(
            timestamp="2026-09-12T00:00:00Z",
            summary="Located PDF file.",
            filesystem=fs,
        )
        ctx_str = unified.format_for_context()
        assert "Located PDF file" in ctx_str
        assert "FS: /tmp/test.pdf" in ctx_str


class TestStructuredCapabilityResult:
    """Tests for structured tool results and backward compatibility."""

    def test_result_summary_and_artifacts(self) -> None:
        res = CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            data={"path": "/home/user/report.pdf", "size": 2048},
            message="Report generated.",
        )
        assert res.summary == "Report generated."
        assert "/home/user/report.pdf" in res.artifacts
        assert res.retryable is True

        d = res.to_dict()
        assert d["summary"] == "Report generated."
        assert "/home/user/report.pdf" in d["artifacts"]
