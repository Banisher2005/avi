"""Unit tests for the ActionCategory risk model and confirmation prompt formatting."""

from avi.execution.models import CommandRequest
from avi.safety.engine import SafetyEngine
from avi.safety.models import ActionCategory, RiskLevel, SafetyAssessment


class TestActionCategoryModel:
    def test_category_values(self):
        assert ActionCategory.READ_ONLY == "READ_ONLY"
        assert ActionCategory.LOW_RISK_ACTION == "LOW_RISK_ACTION"
        assert ActionCategory.EXTERNAL_ACTION == "EXTERNAL_ACTION"
        assert ActionCategory.FILESYSTEM_WRITE == "FILESYSTEM_WRITE"
        assert ActionCategory.DESTRUCTIVE == "DESTRUCTIVE"
        assert ActionCategory.PRIVILEGED == "PRIVILEGED"

    def test_format_confirmation_prompt_by_category(self):
        # LOW_RISK_ACTION
        sa_low = SafetyAssessment(
            level=RiskLevel.CONFIRM,
            category=ActionCategory.LOW_RISK_ACTION,
            reason="Utility process",
            command="sleep 2",
        )
        prompt_low = sa_low.format_confirmation_prompt()
        assert "This action will launch an application or external process." in prompt_low
        assert "Execute? [y/N]" in prompt_low

        # EXTERNAL_ACTION
        sa_ext = SafetyAssessment(
            level=RiskLevel.CONFIRM,
            category=ActionCategory.EXTERNAL_ACTION,
            reason="Network download",
            command="curl https://example.com",
        )
        prompt_ext = sa_ext.format_confirmation_prompt()
        assert "This action will open an external link or service." in prompt_ext

        # DESTRUCTIVE
        sa_dest = SafetyAssessment(
            level=RiskLevel.BLOCK,
            category=ActionCategory.DESTRUCTIVE,
            reason="Root deletion",
            command="rm -rf /",
        )
        prompt_dest = sa_dest.format_confirmation_prompt()
        assert "WARNING: This command performs destructive deletion or modification." in prompt_dest

        # PRIVILEGED
        sa_priv = SafetyAssessment(
            level=RiskLevel.BLOCK,
            category=ActionCategory.PRIVILEGED,
            reason="Root escalation",
            command="sudo reboot",
        )
        prompt_priv = sa_priv.format_confirmation_prompt()
        assert "CRITICAL: This command requires elevated system privileges." in prompt_priv


class TestSafetyEngineCategoryAssignment:
    def setup_method(self):
        self.safety = SafetyEngine()

    def test_read_only_command_category(self):
        req = CommandRequest(program="ls", args=["-la"])
        assessment = self.safety.evaluate(req)
        assert assessment.level == RiskLevel.SAFE
        assert assessment.category == ActionCategory.READ_ONLY

    def test_privileged_command_category(self):
        req = CommandRequest(program="sudo", args=["apt", "update"])
        assessment = self.safety.evaluate(req)
        assert assessment.level == RiskLevel.BLOCK
        assert assessment.category == ActionCategory.PRIVILEGED

    def test_destructive_command_category(self):
        req = CommandRequest(program="rm", args=["-rf", "/"])
        assessment = self.safety.evaluate(req)
        assert assessment.level == RiskLevel.BLOCK
        assert assessment.category == ActionCategory.DESTRUCTIVE

    def test_external_command_category(self):
        req = CommandRequest(program="curl", args=["https://example.com"])
        assessment = self.safety.evaluate(req)
        assert assessment.level == RiskLevel.CONFIRM
        assert assessment.category == ActionCategory.EXTERNAL_ACTION

    def test_low_risk_utility_category(self):
        req = CommandRequest(program="sleep", args=["5"])
        assessment = self.safety.evaluate(req)
        assert assessment.level == RiskLevel.CONFIRM
        assert assessment.category == ActionCategory.LOW_RISK_ACTION

    def test_filesystem_write_category(self):
        req = CommandRequest(program="mkdir", args=["-p", "/tmp/testdir"])
        assessment = self.safety.evaluate(req)
        assert assessment.level == RiskLevel.CONFIRM
        assert assessment.category == ActionCategory.FILESYSTEM_WRITE
