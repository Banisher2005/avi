"""Browser download detection and verification capability."""

from __future__ import annotations

import logging
from typing import Any

from avi.browser.downloads import DownloadsWatcher
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger(__name__)


class BrowserDownloadCapability(BaseCapability):
    """Inspect, detect, or wait for completed browser downloads in the Downloads directory."""

    name = "browser.download"
    description = (
        "Inspect, detect, or verify completed browser downloads in the user's Downloads folder. "
        "Filters out in-progress (.crdownload, .part, .tmp) files and verifies completed file size."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Optional filename pattern or substring to search for (e.g. '*.pdf' or 'report').",
            },
            "timeout": {
                "type": "number",
                "description": "Seconds to wait for an expected download to complete (max 10s, default 0 for immediate check).",
                "default": 0.0,
            },
            "max_age_seconds": {
                "type": "number",
                "description": "Maximum age of recent downloads to return in seconds (default 300s / 5 minutes).",
                "default": 300.0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of recent downloads to return (default 5).",
                "default": 5,
            },
        },
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(self, watcher: DownloadsWatcher | None = None) -> None:
        self.watcher = watcher or DownloadsWatcher()
        self.tags = ("browser", "download", "file", "filesystem", "watch")
        self.aliases = [
            "browser.downloads",
            "check_downloads",
            "get_recent_downloads",
            "detect_download",
        ]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute download inspection or wait."""
        filename = kwargs.get("filename") or kwargs.get("pattern")
        timeout = float(kwargs.get("timeout", 0.0))
        max_age = float(kwargs.get("max_age_seconds", 300.0))
        limit = int(kwargs.get("limit", 5))

        if timeout > 0.0:
            found = self.watcher.wait_for_download(
                pattern=filename,
                timeout=timeout,
            )
            if found:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    message=f"Verified completed download: {found.filename} ({found.size_bytes} bytes).",
                    data=found.to_dict(),
                )
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"No completed download matching '{filename or 'any'}' detected within {timeout:.1f}s.",
                message=f"No download completed within {timeout:.1f}s.",
                data={"pattern": filename, "timeout": timeout},
            )

        # Immediate check
        recent = self.watcher.get_recent_downloads(
            max_age_seconds=max_age,
            pattern=filename,
            limit=limit,
        )

        downloads_list = [d.to_dict() for d in recent]
        count = len(downloads_list)
        summary = (
            f"Found {count} recent download(s)."
            if count > 0
            else "No recent completed downloads found."
        )

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            message=summary,
            data={
                "downloads": downloads_list,
                "count": count,
                "downloads_dir": str(self.watcher.downloads_dir),
            },
        )
