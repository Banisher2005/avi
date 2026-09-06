"""Timer action for native assistant timers and countdowns."""

import time
from typing import Callable

from avi.actions.base import ActionResult, BaseAction
from avi.safety.models import ActionCategory


def format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    total_secs = int(round(seconds))
    if total_secs < 60:
        return f"{total_secs} second{'s' if total_secs != 1 else ''}"
    minutes = total_secs // 60
    rem_secs = total_secs % 60
    if rem_secs == 0:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} min {rem_secs} sec"


class TimerAction(BaseAction):
    """Native timer action that does not rely on shell sleep commands."""

    name = "assistant.timer"
    category = ActionCategory.LOW_RISK_ACTION
    requires_confirmation = False

    def __init__(
        self,
        duration_seconds: float,
        label: str = "",
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.duration_seconds = max(0.0, float(duration_seconds))
        self.label = label.strip()
        self._sleep_fn = sleep_fn or time.sleep

    @property
    def start_message(self) -> str:
        """Friendly message indicating the timer has started."""
        dur_str = format_duration(self.duration_seconds)
        if self.label:
            return f"Timer set for {dur_str} ({self.label})."
        return f"Timer set for {dur_str}."

    def execute(self) -> ActionResult:
        """Run the timer and signal completion."""
        if self.duration_seconds <= 0:
            return ActionResult(
                success=False,
                message="Timer duration must be greater than zero.",
            )

        try:
            self._sleep_fn(self.duration_seconds)
            finish_msg = "Time's up."
            if self.label:
                finish_msg = f"Time's up: {self.label}."
            return ActionResult(
                success=True,
                message=finish_msg,
                data={"duration": self.duration_seconds, "label": self.label},
            )
        except Exception as err:
            return ActionResult(
                success=False,
                message=f"Timer interrupted: {err}",
            )
