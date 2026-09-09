"""Downloads directory watcher and verification for browser computer use."""

from __future__ import annotations

import fnmatch
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PARTIAL_EXTENSIONS = {".crdownload", ".part", ".tmp", ".download"}


@dataclass
class DownloadInfo:
    """Represents a detected downloaded file."""

    path: str
    filename: str
    size_bytes: int
    extension: str
    modified_at: float
    is_complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
            "modified_at": self.modified_at,
            "is_complete": self.is_complete,
        }


class DownloadsWatcher:
    """Monitors and verifies completed files in the user Downloads folder."""

    def __init__(self, downloads_dir: Path | str | None = None) -> None:
        if downloads_dir:
            self.downloads_dir = Path(downloads_dir).expanduser().resolve()
        else:
            xdg = os.environ.get("XDG_DOWNLOAD_DIR")
            if xdg and Path(xdg).is_dir():
                self.downloads_dir = Path(xdg).resolve()
            else:
                self.downloads_dir = (Path.home() / "Downloads").resolve()

    def get_recent_downloads(
        self,
        max_age_seconds: float = 300.0,
        pattern: str | None = None,
        limit: int = 5,
    ) -> list[DownloadInfo]:
        """List recently modified completed files, excluding partial/in-progress downloads."""
        if not self.downloads_dir.exists() or not self.downloads_dir.is_dir():
            return []

        now = time.time()
        results: list[DownloadInfo] = []

        try:
            for entry in self.downloads_dir.iterdir():
                if not entry.is_file():
                    continue

                ext = entry.suffix.lower()
                if ext in PARTIAL_EXTENSIONS or entry.name.startswith("."):
                    continue

                try:
                    stat = entry.stat()
                except (OSError, PermissionError):
                    continue

                # Filter by age if positive
                if max_age_seconds > 0 and (now - stat.st_mtime) > max_age_seconds:
                    continue

                if pattern and not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                    if pattern.lower() not in entry.name.lower():
                        continue

                results.append(
                    DownloadInfo(
                        path=str(entry),
                        filename=entry.name,
                        size_bytes=stat.st_size,
                        extension=ext,
                        modified_at=stat.st_mtime,
                        is_complete=True,
                    )
                )
        except (OSError, PermissionError) as err:
            logger.debug("Error reading downloads dir %s: %s", self.downloads_dir, err)

        # Sort descending by modified time
        results.sort(key=lambda d: d.modified_at, reverse=True)
        return results[:limit]

    def wait_for_download(
        self,
        since_timestamp: float | None = None,
        pattern: str | None = None,
        timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> DownloadInfo | None:
        """Wait boundedly for a new completed download file to appear."""
        start_ts = since_timestamp or time.time()
        deadline = time.time() + min(timeout, 10.0)  # Bound timeout to max 10s

        while time.time() <= deadline:
            recent = self.get_recent_downloads(
                max_age_seconds=max(0.0, time.time() - start_ts + 1.0),
                pattern=pattern,
                limit=1,
            )
            if recent and recent[0].modified_at >= (start_ts - 0.5):
                candidate = recent[0]
                # Verify file is not actively growing (has finished writing)
                try:
                    size1 = Path(candidate.path).stat().st_size
                    time.sleep(0.1)
                    size2 = Path(candidate.path).stat().st_size
                    if size1 == size2 and size1 > 0:
                        candidate.size_bytes = size2
                        return candidate
                except (OSError, PermissionError):
                    pass

            time.sleep(poll_interval)

        return None
