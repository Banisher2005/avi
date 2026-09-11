"""Operational event formatter and honest progress calculations for UI and CLI streaming."""

import re
from typing import Any

from avi.agent.events import ProgressEvent, ProgressEventType


class OperationalEventFormatter:
    """Transforms internal agent lifecycle events into concise user-facing operational messages."""

    @staticmethod
    def strip_chain_of_thought(text: str) -> str:
        """Strip internal thinking/reasoning blocks (e.g. <think>...</think>)."""
        if not text:
            return ""
        # Remove XML-style thinking tags
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
        # Remove markdown thought quotes
        cleaned = re.sub(r"^\s*>.*?thinking.*?\n", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
        return cleaned.strip()

    @classmethod
    def format_event(cls, event: ProgressEvent) -> str:
        """Format a ProgressEvent into a concise user-visible status line."""
        clean_msg = cls.strip_chain_of_thought(event.message or "")
        etype = event.event_type

        if etype == ProgressEventType.TASK_STARTED:
            return "◌ Understanding request..."

        if etype == ProgressEventType.PLANNING:
            return "◌ Planning..."

        if etype == ProgressEventType.PLAN_CREATED:
            count = event.data.get("steps_count")
            if count:
                return f"✓ Plan ready ({count} step{'s' if count != 1 else ''})"
            return "✓ Plan ready"

        if etype == ProgressEventType.CAPABILITY_SELECTED:
            cap = event.capability_name or "tool"
            return f"→ Selected: {cap}"

        if etype == ProgressEventType.STEP_STARTED:
            step_idx = event.step_index
            msg = clean_msg or event.capability_name or "Executing action"
            prefix = f"[{step_idx}] " if step_idx else ""
            return f"→ {prefix}{msg}"

        if etype == ProgressEventType.OBSERVATION_CAPTURED:
            obs = event.data.get("observation") or clean_msg
            if obs and len(str(obs)) < 60:
                return f"✓ Observed: {obs}"
            return "✓ Observation captured"

        if etype == ProgressEventType.STEP_COMPLETED:
            return f"✓ {clean_msg}" if clean_msg else "✓ Step completed"

        if etype == ProgressEventType.STEP_FAILED:
            return f"✗ Step failed: {clean_msg}" if clean_msg else "✗ Step failed"

        if etype == ProgressEventType.FAILURE_DIAGNOSED:
            return f"↻ Diagnosed: {clean_msg}" if clean_msg else "↻ Analyzing failure..."

        if etype == ProgressEventType.RECOVERY_ATTEMPTED:
            return f"↻ Attempting recovery: {clean_msg}" if clean_msg else "↻ Attempting recovery..."

        if etype == ProgressEventType.REPLANNING:
            return "↻ Adapting plan..."

        if etype == ProgressEventType.PLAN_ADAPTED:
            return f"✓ Plan adapted: {clean_msg}" if clean_msg else "✓ Adapted plan ready"

        if etype == ProgressEventType.VERIFICATION_STARTED:
            return "→ Verifying outcome..."

        if etype == ProgressEventType.STATE_CHANGE_VERIFIED or etype == ProgressEventType.VERIFICATION_COMPLETED:
            return "✓ State change verified"

        if etype in (ProgressEventType.GOAL_COMPLETED, ProgressEventType.TASK_COMPLETED):
            return f"✓ {clean_msg}" if clean_msg else "✓ Goal completed successfully."

        if etype in (ProgressEventType.GOAL_FAILED, ProgressEventType.TASK_FAILED):
            return f"✗ {clean_msg}" if clean_msg else "✗ Task failed."

        if etype == ProgressEventType.TASK_PAUSED:
            return f"⏸ {clean_msg}" if clean_msg else "⏸ Task paused."

        if etype == ProgressEventType.TASK_RESUMED:
            return f"▶ {clean_msg}" if clean_msg else "▶ Task resumed."

        if etype == ProgressEventType.TASK_CANCELLED:
            return f"■ {clean_msg}" if clean_msg else "■ Task cancelled."

        if etype in (ProgressEventType.CONFIRMATION_REQUIRED, ProgressEventType.PAUSED_FOR_CONFIRMATION):
            return f"◌ Confirmation required: {clean_msg}"

        return clean_msg

    @staticmethod
    def calculate_progress(completed_steps: int, total_steps: int) -> dict[str, Any]:
        """Compute honest progress representation without fabricated percentages."""
        if total_steps > 0:
            fraction = min(completed_steps / total_steps, 1.0)
            return {
                "is_indeterminate": False,
                "completed": completed_steps,
                "total": total_steps,
                "remaining": max(0, total_steps - completed_steps),
                "fraction": fraction,
                "display": f"{completed_steps}/{total_steps} steps",
            }
        return {
            "is_indeterminate": True,
            "completed": completed_steps,
            "total": 0,
            "remaining": 0,
            "fraction": 0.0,
            "display": "In progress",
        }
