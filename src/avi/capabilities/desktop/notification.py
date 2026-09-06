"""Desktop notification capability using standard Linux notification utilities."""

import shutil
import subprocess
from typing import Any

from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory


class NotificationCapability(BaseCapability):
    """Sends a native desktop notification via notify-send or portal."""

    name = "desktop.notification"
    description = "Send a native desktop notification to the user."
    input_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title or summary of the notification.",
            },
            "message": {
                "type": "string",
                "description": "Body message of the notification.",
            },
            "urgency": {
                "type": "string",
                "description": "Notification urgency level: 'low', 'normal', or 'critical'.",
                "enum": ["low", "normal", "critical"],
                "default": "normal",
            },
        },
        "required": ["title", "message"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def execute(
        self,
        title: str = "AVI",
        message: str = "",
        urgency: str = "normal",
        **kwargs: Any,
    ) -> CapabilityResult:
        """Send notification using notify-send."""
        if not shutil.which("notify-send"):
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="notify-send binary not found on this system.",
                message="Notification utility is not installed.",
                classification=self.data_classification,
            )

        cmd = ["notify-send", "-u", urgency, str(title), str(message)]
        try:
            proc = subprocess.run(
                cmd,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=3.0,
            )
            if proc.returncode == 0:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"title": title, "message": message, "urgency": urgency},
                    message=(
                        f"Notification sent: '{title}' - {message}."
                        if message
                        else f"Notification sent: '{title}'."
                    ),
                    classification=self.data_classification,
                )
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=proc.stderr.decode("utf-8", errors="replace").strip(),
                message="Failed to dispatch notification.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Notification error: {err}",
                classification=self.data_classification,
            )
