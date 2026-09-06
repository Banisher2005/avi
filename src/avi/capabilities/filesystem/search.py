"""Filesystem search capability for finding files and directories."""

import os
from pathlib import Path
from typing import Any

from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory
from avi.tools.filesystem import format_bytes


class FilesystemSearchCapability(BaseCapability):
    """Searches local directories for files by pattern, extension, and recency."""

    name = "filesystem.search"
    description = "Search local files by name pattern, file extension, and modification time."
    input_schema = {
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "Directory to search within (e.g. '~/Downloads', '~/Documents', '.').",
                "default": ".",
            },
            "pattern": {
                "type": "string",
                "description": "File name substring or glob pattern to match (e.g. '*.pdf', 'invoice').",
                "default": "*",
            },
            "extension": {
                "type": "string",
                "description": "Optional file extension to filter by (e.g. 'pdf', 'png', 'txt').",
            },
            "newest_first": {
                "type": "boolean",
                "description": "Sort results with most recently modified files first.",
                "default": True,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (1-50).",
                "default": 10,
            },
        },
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def execute(
        self,
        directory: str = ".",
        pattern: str = "*",
        extension: str | None = None,
        newest_first: bool = True,
        limit: int = 10,
        **kwargs: Any,
    ) -> CapabilityResult:
        """Execute safe search across directory."""
        target_dir = kwargs.get("path") or kwargs.get("dir") or directory
        dir_path = Path(target_dir).expanduser().resolve()
        if not dir_path.exists():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Directory does not exist: {dir_path}",
                message=f"Directory '{target_dir}' does not exist.",
                classification=self.data_classification,
            )
        if not dir_path.is_dir():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Path is not a directory: {dir_path}",
                message=f"Path '{target_dir}' is not a directory.",
                classification=self.data_classification,
            )

        limit = max(1, min(50, int(limit)))
        norm_ext = extension.lower().lstrip(".") if extension else None
        clean_pat = (kwargs.get("query") or pattern).strip().lower()

        matches = []
        try:
            # Bounded walk up to 3 levels deep to prevent freezing on large trees
            max_depth = 3
            base_depth = len(dir_path.parts)

            for root, dirs, files in os.walk(dir_path):
                current_depth = len(Path(root).parts) - base_depth
                if current_depth > max_depth:
                    dirs.clear()
                    continue

                for file_name in files:
                    # Filter by extension if specified
                    if norm_ext and not file_name.lower().endswith(f".{norm_ext}"):
                        continue

                    # Filter by pattern
                    if clean_pat and clean_pat != "*":
                        if clean_pat not in file_name.lower():
                            continue

                    full_path = Path(root) / file_name
                    try:
                        stat = full_path.stat()
                        matches.append(
                            {
                                "name": file_name,
                                "path": str(full_path),
                                "size_bytes": stat.st_size,
                                "size_formatted": format_bytes(stat.st_size),
                                "modified_timestamp": stat.st_mtime,
                            }
                        )
                    except (OSError, PermissionError):
                        continue

        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Filesystem search failed: {err}",
                classification=self.data_classification,
            )

        if newest_first:
            matches.sort(key=lambda item: item["modified_timestamp"], reverse=True)

        selected = matches[:limit]
        count = len(selected)

        if count == 0:
            msg = f"No files found matching '{pattern}' in {dir_path.name}."
        elif count == 1:
            top = selected[0]
            msg = f"Found 1 file: {top['name']} ({top['size_formatted']}) in {dir_path.name}."
        else:
            top = selected[0]
            msg = (
                f"Found {len(matches)} matching file(s) in {dir_path.name}. "
                f"Newest: {top['name']} ({top['size_formatted']})."
            )

        return CapabilityResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            data={
                "directory": str(dir_path),
                "total_found": len(matches),
                "results": selected,
                "matches": selected,
            },
            message=msg,
            classification=self.data_classification,
        )
