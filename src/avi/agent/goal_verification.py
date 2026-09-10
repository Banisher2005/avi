"""Goal verification subsystem for validating real-world postconditions of completed goals."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avi.agent.models import Goal, GoalSegment, PlanStep, StepStatus

logger = logging.getLogger("avi.agent.goal_verification")


@dataclass
class GoalVerificationResult:
    """Outcome of validating postconditions for a goal or goal segment."""

    verified: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "reason": self.reason,
            "details": self.details,
        }


class GoalVerifier:
    """Verifies that actual environmental outcomes match the intended goal postconditions."""

    def verify_segment(
        self,
        segment: GoalSegment,
        completed_steps: list[PlanStep],
        current_observation: dict[str, Any] | None = None,
    ) -> GoalVerificationResult:
        """Verify an individual goal milestone segment."""
        current_observation = current_observation or {}

        # 1. If explicit verification conditions exist on segment, test them
        cond = segment.verification_condition or {}
        if cond:
            file_exists = cond.get("file_exists")
            if file_exists:
                p = Path(file_exists).expanduser()
                if not p.exists():
                    return GoalVerificationResult(
                        verified=False,
                        reason=f"Expected file '{file_exists}' does not exist.",
                        details={"expected_file": file_exists},
                    )
                if cond.get("non_empty") and p.stat().st_size == 0:
                    return GoalVerificationResult(
                        verified=False,
                        reason=f"File '{file_exists}' exists but is empty.",
                        details={"file_size": 0},
                    )

            dir_exists = cond.get("dir_exists")
            if dir_exists:
                p = Path(dir_exists).expanduser()
                if not p.is_dir():
                    return GoalVerificationResult(
                        verified=False,
                        reason=f"Expected directory '{dir_exists}' does not exist.",
                        details={"expected_dir": dir_exists},
                    )

            target_url = cond.get("url")
            if target_url:
                obs_url = current_observation.get("url", "")
                if target_url not in obs_url:
                    return GoalVerificationResult(
                        verified=False,
                        reason=f"Browser is not at expected URL '{target_url}'. Current: '{obs_url}'.",
                        details={"current_url": obs_url, "expected_url": target_url},
                    )

        # 2. If no explicit conditions, check that completed steps finished successfully and verified
        if not completed_steps:
            return GoalVerificationResult(
                verified=False,
                reason="No steps were executed for segment.",
            )

        failed_steps = [s for s in completed_steps if s.status != StepStatus.SUCCESS]
        if failed_steps:
            return GoalVerificationResult(
                verified=False,
                reason=f"One or more steps failed: {[s.capability_name for s in failed_steps]}",
                details={"failed_steps": [s.step_id for s in failed_steps]},
            )

        # 3. Inspect artifacts produced by completed steps
        for step in completed_steps:
            if step.artifact_path:
                p = Path(step.artifact_path).expanduser()
                if not p.exists():
                    return GoalVerificationResult(
                        verified=False,
                        reason=f"Step artifact '{step.artifact_path}' missing.",
                        details={"missing_artifact": step.artifact_path},
                    )

        return GoalVerificationResult(
            verified=True,
            reason=f"Segment '{segment.title or segment.segment_id}' postconditions verified.",
            details={"step_count": len(completed_steps)},
        )

    def verify_goal(
        self,
        goal: Goal,
        current_observation: dict[str, Any] | None = None,
    ) -> GoalVerificationResult:
        """Verify the full goal by verifying all of its segments."""
        if not goal.segments:
            return GoalVerificationResult(
                verified=False,
                reason="Goal has no segments defined.",
            )

        for seg in goal.segments:
            res = self.verify_segment(seg, seg.completed_steps, current_observation)
            if not res.verified:
                return GoalVerificationResult(
                    verified=False,
                    reason=f"Segment '{seg.title or seg.segment_id}' unverified: {res.reason}",
                    details={"failed_segment_id": seg.segment_id, "segment_details": res.details},
                )

        return GoalVerificationResult(
            verified=True,
            reason=f"All {len(goal.segments)} segment postconditions verified.",
            details={"segments_verified": len(goal.segments)},
        )
