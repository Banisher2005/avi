"""Failure diagnosis and classification subsystem for adaptive agent execution."""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from avi.agent.models import FailureCategory, PlanStep
from avi.capabilities.models import CapabilityResult, ExecutionStatus

logger = logging.getLogger("avi.agent.diagnosis")


@dataclass
class DiagnosisResult:
    """Diagnostic analysis of an action failure with recommended recovery strategy."""

    category: FailureCategory
    root_cause: str
    suggested_recovery: str
    recoverable: bool = True
    retry_delay_s: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "root_cause": self.root_cause,
            "suggested_recovery": self.suggested_recovery,
            "recoverable": self.recoverable,
            "retry_delay_s": self.retry_delay_s,
            "details": self.details,
        }


class FailureDiagnoser:
    """Diagnoses capability and plan execution failures to guide adaptive replanning."""

    def diagnose(
        self,
        step: PlanStep,
        result: CapabilityResult,
        pre_observation: dict[str, Any] | None = None,
        post_observation: dict[str, Any] | None = None,
        consecutive_failures: int = 0,
        loop_detected: bool = False,
    ) -> DiagnosisResult:
        """Analyze failure context and categorize the root cause."""
        err_msg = (result.error or result.message or "").strip().lower()
        cap = (step.capability_name or "").lower()

        # 1. Confirmation required
        if (
            result.status == ExecutionStatus.CONFIRMATION_REQUIRED
            or "confirmation required" in err_msg
            or "requires confirmation" in err_msg
        ):
            return DiagnosisResult(
                category=FailureCategory.CONFIRMATION_REQUIRED,
                root_cause="Action requires user approval before executing.",
                suggested_recovery="pause_for_confirmation",
                recoverable=True,
                retry_delay_s=0.0,
            )

        # 2. Repeated failure / loop detection
        if loop_detected or consecutive_failures >= 2 or "loop detected" in err_msg:
            return DiagnosisResult(
                category=FailureCategory.REPEATED_FAILURE,
                root_cause="Repeated failed attempts with no progress or loop detected.",
                suggested_recovery="switch_strategy",
                recoverable=False,
                retry_delay_s=0.0,
            )

        # 3. Unsupported capability / missing binary
        unsupported_patterns = [
            r"command not found",
            r"not installed",
            r"no binary found",
            r"unsupported",
            r"not implemented",
            r"missing dependency",
            r"feature unavailable",
        ]
        if any(re.search(pat, err_msg) for pat in unsupported_patterns):
            return DiagnosisResult(
                category=FailureCategory.UNSUPPORTED_CAPABILITY,
                root_cause=f"Capability or underlying system tool is unsupported: {result.error or result.message}",
                suggested_recovery="use_alternative_capability",
                recoverable=True,
                retry_delay_s=0.0,
            )

        # 4. Navigation failure
        nav_patterns = [
            r"net::",
            r"err_connection",
            r"dns_probe",
            r"navigation failed",
            r"connection refused",
            r"cannot reach",
            r"address unreachable",
            r"host not found",
        ]
        if any(re.search(pat, err_msg) for pat in nav_patterns) or (
            "navigate" in cap and "failed" in err_msg
        ):
            return DiagnosisResult(
                category=FailureCategory.NAVIGATION_FAILURE,
                root_cause=f"Network or navigation error occurred: {result.error or result.message}",
                suggested_recovery="verify_url_or_search",
                recoverable=True,
                retry_delay_s=1.0,
            )

        # 5. Stale element / state mismatch
        stale_patterns = [
            r"stale",
            r"detached",
            r"node not found",
            r"element is no longer attached",
            r"context destroyed",
            r"target closed",
            r"window closed",
            r"session closed",
        ]
        if any(re.search(pat, err_msg) for pat in stale_patterns):
            return DiagnosisResult(
                category=FailureCategory.STALE_STATE,
                root_cause="The target element or window state changed or detached.",
                suggested_recovery="refresh_and_reobserve",
                recoverable=True,
                retry_delay_s=0.5,
            )

        # 6. Temporary loading / timeout
        loading_patterns = [
            r"loading",
            r"busy",
            r"timed out",
            r"timeout",
            r"still loading",
            r"wait timeout",
        ]
        if any(re.search(pat, err_msg) for pat in loading_patterns):
            return DiagnosisResult(
                category=FailureCategory.TEMPORARY_LOADING,
                root_cause="Target is still loading or timed out waiting for response.",
                suggested_recovery="wait_and_reobserve",
                recoverable=True,
                retry_delay_s=1.0,
            )

        # 7. Wrong assumption (element not found, file not found, selector mismatch)
        assumption_patterns = [
            r"element not found",
            r"could not find",
            r"no element matching",
            r"no such file",
            r"file not found",
            r"does not exist",
            r"cannot find",
            r"target not found",
            r"invalid selector",
            r"not visible",
        ]
        if any(re.search(pat, err_msg) for pat in assumption_patterns) or (
            "open_file" in cap and ("not exist" in err_msg or "file not found" in err_msg)
        ):
            recovery = (
                "search_filesystem"
                if "file" in cap or "filesystem" in cap
                else "reobserve_and_replan"
            )
            return DiagnosisResult(
                category=FailureCategory.WRONG_ASSUMPTION,
                root_cause=f"Target was not where the plan assumed: {result.error or result.message}",
                suggested_recovery=recovery,
                recoverable=True,
                retry_delay_s=0.2,
            )

        # 8. Default: Execution error
        return DiagnosisResult(
            category=FailureCategory.EXECUTION_ERROR,
            root_cause=result.error or result.message or "Unknown execution error.",
            suggested_recovery="retry_or_replan",
            recoverable=True,
            retry_delay_s=0.5,
        )
