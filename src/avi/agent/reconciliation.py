"""Environment reconciliation subsystem for resuming persistent agent tasks."""

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from avi.agent.models import PlanStep, StepStatus

logger = logging.getLogger("avi.agent.reconciliation")


class ReconciliationStatus(str, Enum):
    """Classification of environment state relative to a resumed task."""

    UNCHANGED = "unchanged"
    PARTIALLY_CHANGED = "partially_changed"
    ALREADY_COMPLETE = "already_complete"
    STALE = "stale"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


@dataclass
class ReconciliationReport:
    """Consolidated findings of an environment reconciliation inspection."""

    status: ReconciliationStatus
    satisfied_steps: list[int] = field(default_factory=list)
    conflicting_steps: list[int] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    recommendation: str = "proceed"  # proceed, skip_satisfied, replan, complete

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "satisfied_steps": self.satisfied_steps,
            "conflicting_steps": self.conflicting_steps,
            "details": self.details,
            "recommendation": self.recommendation,
        }


class EnvironmentReconciler:
    """Evaluates environmental deltas between recorded task state and actual world state."""

    def reconcile(
        self,
        completed_steps: list[PlanStep],
        pending_steps: list[PlanStep],
        current_observation: dict[str, Any] | None = None,
    ) -> ReconciliationReport:
        """Inspect environment state against completed and pending plan steps."""
        current_observation = current_observation or {}
        details: dict[str, Any] = {"inspected_steps": len(completed_steps) + len(pending_steps)}

        # 1. Verify that previous completed steps haven't been invalidated / reverted
        conflicting_steps: list[int] = []
        for step in completed_steps:
            conflict = self._check_step_conflict(step)
            if conflict:
                conflicting_steps.append(step.step_id)
                details[f"step_{step.step_id}_conflict"] = conflict

        if conflicting_steps:
            logger.warning("Reconciliation found conflicts in completed steps: %s", conflicting_steps)
            return ReconciliationReport(
                status=ReconciliationStatus.CONFLICTING,
                conflicting_steps=conflicting_steps,
                details=details,
                recommendation="replan",
            )

        # 2. Check which pending steps are ALREADY satisfied in the environment
        satisfied_steps: list[int] = []
        for step in pending_steps:
            is_satisfied, reason = self._is_step_satisfied(step, current_observation)
            if is_satisfied:
                satisfied_steps.append(step.step_id)
                details[f"step_{step.step_id}_satisfied"] = reason

        # 3. Determine overall reconciliation status
        if pending_steps and len(satisfied_steps) == len(pending_steps):
            logger.info("All pending steps are already satisfied in the environment.")
            return ReconciliationReport(
                status=ReconciliationStatus.ALREADY_COMPLETE,
                satisfied_steps=satisfied_steps,
                details=details,
                recommendation="complete",
            )
        elif satisfied_steps:
            logger.info("Some pending steps are already satisfied: %s", satisfied_steps)
            return ReconciliationReport(
                status=ReconciliationStatus.PARTIALLY_CHANGED,
                satisfied_steps=satisfied_steps,
                details=details,
                recommendation="skip_satisfied",
            )

        return ReconciliationReport(
            status=ReconciliationStatus.UNCHANGED,
            satisfied_steps=[],
            details=details,
            recommendation="proceed",
        )

    def _check_step_conflict(self, step: PlanStep) -> str | None:
        """Check whether an already completed step had its side effects reversed."""
        cap = step.capability_name.lower().strip()
        args = step.arguments or {}

        # Filesystem checks
        if "filesystem" in cap or "file" in cap:
            target_path = args.get("destination") or args.get("path") or args.get("target")
            if target_path:
                try:
                    p = Path(target_path).expanduser()
                    if "delete" in cap or "trash" in cap:
                        if p.exists():
                            return f"Path {target_path} was supposed to be deleted, but still exists"
                    elif "create_directory" in cap or "mkdir" in cap:
                        if not p.is_dir():
                            return f"Directory {target_path} was created but is no longer a directory"
                    elif "write" in cap or "append" in cap or "touch" in cap:
                        if not p.is_file():
                            return f"File {target_path} was written but no longer exists as a file"
                    elif "move" in cap or "copy" in cap:
                        if not p.exists():
                            return f"Target {target_path} of move/copy no longer exists"
                except Exception as ex:
                    return f"Error checking path {target_path}: {ex}"

        return None

    def _is_step_satisfied(
        self, step: PlanStep, current_observation: dict[str, Any]
    ) -> tuple[bool, str]:
        """Check if a pending step's desired postcondition is already present."""
        if step.status == StepStatus.SUCCESS and step.verified:
            return True, "Step was already verified as successful"

        cap = step.capability_name.lower().strip()
        args = step.arguments or {}

        # Filesystem idempotency
        if "filesystem" in cap or "file" in cap:
            if "create_directory" in cap or "mkdir" in cap:
                path = args.get("path") or args.get("destination")
                if path and os.path.isdir(os.path.expanduser(path)):
                    return True, f"Directory {path} already exists"

            if "write" in cap:
                path = args.get("path")
                content = args.get("content")
                if path and content is not None:
                    p = Path(path).expanduser()
                    if p.is_file():
                        try:
                            if p.read_text(encoding="utf-8") == content:
                                return True, f"File {path} already contains target content"
                        except Exception:
                            pass

            if "move" in cap:
                source = args.get("source") or args.get("path")
                dest = args.get("destination") or args.get("target")
                if source and dest:
                    src_p = Path(source).expanduser()
                    dest_p = Path(dest).expanduser()
                    if dest_p.exists() and not src_p.exists():
                        return True, f"Target {dest} exists and source {source} is moved"

            if "delete" in cap or "remove" in cap:
                path = args.get("path") or args.get("target")
                if path and not Path(path).expanduser().exists():
                    return True, f"Target {path} is already deleted"

        # Browser URL navigation idempotency
        if "navigate" in cap or "open_url" in cap:
            target_url = args.get("url", "").rstrip("/")
            obs_url = current_observation.get("url", "").rstrip("/")
            if target_url and obs_url and (target_url == obs_url or obs_url.startswith(target_url)):
                return True, f"Browser is already at URL {obs_url}"

        return False, ""
