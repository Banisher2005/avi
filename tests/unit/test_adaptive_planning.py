"""Unit tests for Phase 6 Adaptive Planning, Diagnosis, Verification, and Memory Integration."""

from unittest.mock import MagicMock

from avi.agent.adaptive_planner import AdaptivePlanner
from avi.agent.diagnosis import DiagnosisResult, FailureDiagnoser
from avi.agent.models import FailureCategory, PlanStep
from avi.agent.planner import _normalize_search_dir
from avi.agent.tool_selection import ToolSelector
from avi.agent.verification import StateChangeDetector
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.memory.models import Memory


class TestFailureDiagnosis:
    """Test failure diagnosis across all FailureCategory enums."""

    def test_diagnose_confirmation_required(self):
        diagnoser = FailureDiagnoser()
        step = PlanStep(
            step_id=1, capability_name="desktop.trash_file", arguments={"path": "/tmp/test"}
        )
        res = CapabilityResult(
            success=False,
            status=ExecutionStatus.CONFIRMATION_REQUIRED,
            message="Confirmation required to trash file.",
        )
        diag = diagnoser.diagnose(step, res)
        assert diag.category == FailureCategory.CONFIRMATION_REQUIRED
        assert diag.suggested_recovery == "pause_for_confirmation"
        assert diag.recoverable is True

    def test_diagnose_repeated_failure(self):
        diagnoser = FailureDiagnoser()
        step = PlanStep(
            step_id=1, capability_name="browser.click", arguments={"selector": "#submit"}
        )
        res = CapabilityResult(success=False, status=ExecutionStatus.FAILED, error="Some error")
        diag = diagnoser.diagnose(step, res, loop_detected=True)
        assert diag.category == FailureCategory.REPEATED_FAILURE
        assert diag.recoverable is False

    def test_diagnose_unsupported_capability(self):
        diagnoser = FailureDiagnoser()
        step = PlanStep(
            step_id=1, capability_name="desktop.launch_app", arguments={"app_name": "spotify"}
        )
        res = CapabilityResult(
            success=False, status=ExecutionStatus.FAILED, error="Command not found: spotify"
        )
        diag = diagnoser.diagnose(step, res)
        assert diag.category == FailureCategory.UNSUPPORTED_CAPABILITY
        assert diag.suggested_recovery == "use_alternative_capability"

    def test_diagnose_navigation_failure(self):
        diagnoser = FailureDiagnoser()
        step = PlanStep(
            step_id=1,
            capability_name="browser.navigate",
            arguments={"url": "http://invalid.domain"},
        )
        res = CapabilityResult(
            success=False, status=ExecutionStatus.FAILED, error="net::ERR_NAME_NOT_RESOLVED"
        )
        diag = diagnoser.diagnose(step, res)
        assert diag.category == FailureCategory.NAVIGATION_FAILURE
        assert diag.suggested_recovery == "verify_url_or_search"

    def test_diagnose_stale_state(self):
        diagnoser = FailureDiagnoser()
        step = PlanStep(step_id=1, capability_name="browser.click", arguments={"selector": ".btn"})
        res = CapabilityResult(
            success=False, status=ExecutionStatus.FAILED, error="Node is detached from document"
        )
        diag = diagnoser.diagnose(step, res)
        assert diag.category == FailureCategory.STALE_STATE
        assert diag.suggested_recovery == "refresh_and_reobserve"

    def test_diagnose_temporary_loading(self):
        diagnoser = FailureDiagnoser()
        step = PlanStep(step_id=1, capability_name="browser.observe", arguments={})
        res = CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error="Operation timed out waiting for DOM",
        )
        diag = diagnoser.diagnose(step, res)
        assert diag.category == FailureCategory.TEMPORARY_LOADING
        assert diag.suggested_recovery == "wait_and_reobserve"

    def test_diagnose_wrong_assumption(self):
        diagnoser = FailureDiagnoser()
        step = PlanStep(
            step_id=1, capability_name="desktop.open_file", arguments={"path": "~/report.pdf"}
        )
        res = CapabilityResult(
            success=False, status=ExecutionStatus.FAILED, error="File does not exist: ~/report.pdf"
        )
        diag = diagnoser.diagnose(step, res)
        assert diag.category == FailureCategory.WRONG_ASSUMPTION
        assert diag.suggested_recovery == "search_filesystem"


class TestStateChangeDetection:
    """Test state change detection across environmental actions."""

    def test_read_only_capability_marked_as_changed(self):
        detector = StateChangeDetector()
        res = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, data={"text": "content"}
        )
        change = detector.detect_change("browser.extract", {}, res)
        assert change.changed is True
        assert change.details.get("read_only") is True

    def test_browser_navigation_url_change(self):
        detector = StateChangeDetector()
        res = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, data={"url": "https://example.com"}
        )
        change = detector.detect_change(
            "browser.navigate",
            {"url": "https://example.com"},
            res,
            pre_observation={"url": "about:blank"},
            post_observation={"url": "https://example.com"},
        )
        assert change.changed is True
        assert "Navigation changed URL" in change.description

    def test_browser_scroll_position_change(self):
        detector = StateChangeDetector()
        res = CapabilityResult(
            success=True, status=ExecutionStatus.SUCCESS, data={"direction": "down"}
        )
        change = detector.detect_change(
            "browser.scroll",
            {"direction": "down", "amount": 300},
            res,
            pre_observation={"viewport": {"scroll_y": 0}},
            post_observation={"viewport": {"scroll_y": 300}},
        )
        assert change.changed is True

    def test_browser_click_mutates_dom(self):
        detector = StateChangeDetector()
        res = CapabilityResult(success=True, status=ExecutionStatus.SUCCESS, data={"clicked": True})
        change = detector.detect_change(
            "browser.click",
            {"selector": "button#submit"},
            res,
            pre_observation={"interactive_elements": [1, 2, 3], "url": "https://a.com"},
            post_observation={"interactive_elements": [1, 2, 3, 4, 5], "url": "https://a.com"},
        )
        assert change.changed is True
        assert "mutated" in change.description


class TestAdaptivePlanner:
    """Test adaptive recovery plan synthesis."""

    def test_replan_on_file_not_found_searches_filesystem(self):
        planner = AdaptivePlanner()
        completed = []
        failed = PlanStep(
            step_id=1,
            capability_name="desktop.open_file",
            arguments={"path": "~/Downloads/taxes.pdf"},
            description="Open taxes.pdf",
        )
        diag = DiagnosisResult(
            category=FailureCategory.WRONG_ASSUMPTION,
            root_cause="File not found",
            suggested_recovery="search_filesystem",
        )
        new_plan = planner.replan(
            goal="find and open taxes.pdf",
            completed_steps=completed,
            failed_step=failed,
            diagnosis=diag,
        )
        assert new_plan is not None
        assert len(new_plan.steps) == 2
        assert new_plan.steps[0].capability_name == "filesystem.search"
        assert "taxes.pdf" in new_plan.steps[0].arguments.get("pattern", "")
        assert new_plan.steps[1].capability_name == "desktop.open_file"
        assert new_plan.steps[1].pipe_from_step == 1
        assert new_plan.strategy_name == "filesystem_search_recovery"

    def test_replan_prevents_repeating_failed_strategy(self):
        planner = AdaptivePlanner()
        completed = []
        failed = PlanStep(
            step_id=1,
            capability_name="desktop.open_file",
            arguments={"path": "~/Downloads/taxes.pdf"},
            description="Open taxes.pdf",
        )
        diag = DiagnosisResult(
            category=FailureCategory.WRONG_ASSUMPTION,
            root_cause="File not found",
            suggested_recovery="search_filesystem",
        )
        # Attempt when filesystem_search_recovery was already attempted
        new_plan = planner.replan(
            goal="find and open taxes.pdf",
            completed_steps=completed,
            failed_step=failed,
            diagnosis=diag,
            attempted_strategies=["filesystem_search_recovery"],
        )
        # Cannot repeat already failed strategy
        assert new_plan is None

    def test_replan_browser_stale_element_reobserves(self):
        planner = AdaptivePlanner()
        completed = []
        failed = PlanStep(
            step_id=1,
            capability_name="browser.click",
            arguments={"selector": "#checkout-btn"},
            description="Click checkout button",
        )
        diag = DiagnosisResult(
            category=FailureCategory.STALE_STATE,
            root_cause="Node detached",
            suggested_recovery="refresh_and_reobserve",
        )
        new_plan = planner.replan(
            goal="click checkout button",
            completed_steps=completed,
            failed_step=failed,
            diagnosis=diag,
        )
        assert new_plan is not None
        assert len(new_plan.steps) == 2
        assert new_plan.steps[0].capability_name == "browser.observe"
        assert new_plan.steps[1].capability_name == "browser.click"
        assert new_plan.strategy_name == "browser_reobserve_and_target"

    def test_replan_unsupported_app_launches_web(self):
        planner = AdaptivePlanner()
        completed = []
        failed = PlanStep(
            step_id=1,
            capability_name="desktop.launch_app",
            arguments={"app_name": "slack"},
            description="Launch Slack",
        )
        diag = DiagnosisResult(
            category=FailureCategory.UNSUPPORTED_CAPABILITY,
            root_cause="App not found",
            suggested_recovery="use_alternative_capability",
        )
        new_plan = planner.replan(
            goal="open slack",
            completed_steps=completed,
            failed_step=failed,
            diagnosis=diag,
        )
        assert new_plan is not None
        assert len(new_plan.steps) == 1
        assert new_plan.steps[0].capability_name == "desktop.open_url"
        assert "slack.com" in new_plan.steps[0].arguments.get("url", "")
        assert new_plan.strategy_name == "web_app_fallback"


class TestMemoryAwarePlanning:
    """Test directory and preference resolution with memory context."""

    def test_normalize_search_dir_with_memory_folder_preference(self):
        mem = Memory(
            id="pref_folder_projects",
            content="Preferred projects folder is /home/abhinav/my_code",
            category="path",
            metadata={"folder": "projects", "path": "/home/abhinav/my_code"},
        )
        ctx = {"memories": [mem]}
        resolved = _normalize_search_dir("projects", context=ctx)
        assert resolved == "/home/abhinav/my_code"

    def test_normalize_search_dir_defaults_cleanly(self):
        resolved = _normalize_search_dir("documents")
        assert resolved == "~/Documents"


class TestToolSelectorAdaptiveAdjustments:
    """Test ToolSelector observation and failure score boosts."""

    def test_tool_selector_boosts_search_on_wrong_assumption(self):
        reg = MagicMock(spec=CapabilityRegistry)
        catalog = [
            {
                "name": "filesystem.search",
                "description": "Search filesystem for files",
                "tags": ["filesystem", "search"],
            },
            {
                "name": "desktop.open_file",
                "description": "Open a file",
                "tags": ["desktop", "file"],
            },
        ]
        reg.get_model_catalog.return_value = catalog
        selector = ToolSelector(registry=reg)
        caps = selector.select_capabilities(
            prompt="open document",
            context={"failure_category": "wrong_assumption"},
            limit=2,
        )
        assert any(c["name"] == "filesystem.search" for c in caps)
