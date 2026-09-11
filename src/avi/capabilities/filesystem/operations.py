"""Safe filesystem mutating capabilities with strict risk classification."""

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from avi.capabilities.models import (
    BaseCapability,
    CapabilityCategory,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory
from avi.tools.filesystem import format_bytes


class CreateDirectoryCapability(BaseCapability):
    """Creates a directory at the specified path."""

    name = "filesystem.create_directory"
    description = "Create a new folder or directory path."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path of the directory to create.",
            }
        },
        "required": ["path"],
    }
    risk_category = ActionCategory.FILESYSTEM_WRITE
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True

    def execute(self, path: str, **kwargs: Any) -> CapabilityResult:
        target = Path(path).expanduser().resolve()
        try:
            target.mkdir(parents=True, exist_ok=True)
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"path": str(target)},
                message=f"Created directory {target}.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to create directory {path}: {err}",
                classification=self.data_classification,
            )


class CopyFileCapability(BaseCapability):
    """Copies a file from source to destination."""

    name = "filesystem.copy"
    description = "Copy a file from source path to destination path."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Path to source file."},
            "destination": {"type": "string", "description": "Destination file or folder path."},
        },
        "required": ["source", "destination"],
    }
    risk_category = ActionCategory.FILESYSTEM_WRITE
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True

    def execute(self, source: str, destination: str, **kwargs: Any) -> CapabilityResult:
        src = Path(source).expanduser().resolve()
        dest = Path(destination).expanduser().resolve()

        if not src.exists():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Source file does not exist: {src}",
                message=f"Cannot copy: source '{source}' does not exist.",
                classification=self.data_classification,
            )

        try:
            target_path = dest / src.name if dest.is_dir() else dest
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
            final_path = target_path if target_path.exists() else dest
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"source": str(src), "destination": str(dest), "path": str(final_path)},
                message=f"Copied {src.name} to {dest}.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to copy '{source}' to '{destination}': {err}",
                classification=self.data_classification,
            )


class MoveFileCapability(BaseCapability):
    """Moves or renames a file or directory."""

    name = "filesystem.move"
    description = "Move or rename a file or directory from source to destination."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Path to source file or directory."},
            "destination": {"type": "string", "description": "Destination file or folder path."},
        },
        "required": ["source", "destination"],
    }
    risk_category = ActionCategory.FILESYSTEM_WRITE
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True

    def execute(self, source: str, destination: str, **kwargs: Any) -> CapabilityResult:
        src = Path(source).expanduser().resolve()
        dest = Path(destination).expanduser().resolve()

        if not src.exists():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Source does not exist: {src}",
                message=f"Cannot move: source '{source}' does not exist.",
                classification=self.data_classification,
            )

        # Ensure parent destination directory exists if destination is a file path
        if not dest.exists() and dest.parent.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            target_path = dest / src.name if dest.is_dir() else dest
            shutil.move(str(src), str(dest))
            final_path = target_path if target_path.exists() else dest
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"source": str(src), "destination": str(dest), "path": str(final_path)},
                message=f"Moved {src.name} to {dest}.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to move '{source}' to '{destination}': {err}",
                classification=self.data_classification,
            )


class DeleteFileCapability(BaseCapability):
    """Deletes a file or directory. Strictly DESTRUCTIVE; requires explicit user confirmation."""

    name = "filesystem.delete"
    description = "Permanently delete a file or directory. Requires user confirmation."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of file or directory to delete."}
        },
        "required": ["path"],
    }
    risk_category = ActionCategory.DESTRUCTIVE
    data_classification = DataClassification.USER_CONFIRMATION_REQUIRED
    requires_confirmation = True
    side_effects = True

    def execute(self, path: str, confirmed: bool = False, **kwargs: Any) -> CapabilityResult:
        target = Path(path).expanduser().resolve()

        if not target.exists():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Target does not exist: {target}",
                message=f"Cannot delete: '{path}' does not exist.",
                classification=self.data_classification,
            )

        # Refuse to delete root or home directly
        if str(target) in ("/", str(Path.home())):
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Refusing to delete system root or user home directory.",
                message="Cannot delete system root or user home directory.",
                classification=self.data_classification,
            )

        if not confirmed:
            target_type = "folder" if target.is_dir() else "file"
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.CONFIRMATION_REQUIRED,
                data={"path": str(target), "is_dir": target.is_dir()},
                message=f"This will permanently delete {target_type} '{target.name}'. Do you want to continue?",
                classification=self.data_classification,
            )

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"path": str(target)},
                message=f"Permanently deleted '{target.name}'.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to delete '{path}': {err}",
                classification=self.data_classification,
            )


class WriteFileCapability(BaseCapability):
    """Write or append text content to a local file."""

    name = "filesystem.write_file"
    description = "Write or append text content to a local file."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file path to write."},
            "content": {"type": "string", "description": "Text content to write."},
            "append": {"type": "boolean", "description": "Append to file instead of overwriting.", "default": False},
        },
        "required": ["path", "content"],
    }
    risk_category = ActionCategory.FILESYSTEM_WRITE
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True
    tags = ("filesystem", "write", "file", "save")

    def execute(self, path: str = "", content: str = "", append: bool = False, **kwargs: Any) -> CapabilityResult:
        raw_path = path or kwargs.get("file") or kwargs.get("destination") or kwargs.get("target")
        if not raw_path:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Path parameter is required for write_file.",
                message="Cannot write: no file path specified.",
            )
        target = Path(raw_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        text_content = str(content if content is not None else kwargs.get("text", ""))
        mode = "a" if append else "w"
        try:
            with open(target, mode, encoding=kwargs.get("encoding", "utf-8")) as f:
                f.write(text_content)
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={
                    "path": str(target),
                    "file": str(target),
                    "bytes_written": len(text_content.encode("utf-8")),
                    "append": append,
                },
                message=f"{'Appended to' if append else 'Saved'} {target.name} ({len(text_content)} characters).",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to write to '{target}': {err}",
                classification=self.data_classification,
            )


class ReadFileCapability(BaseCapability):
    """Read text content from a local file."""

    name = "filesystem.read_file"
    description = "Read text content from a local file."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to file to read."},
            "max_lines": {"type": "integer", "description": "Maximum lines to read (default 100).", "default": 100},
        },
        "required": ["path"],
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = False
    supports_observation = True
    tags = ("filesystem", "read", "file", "view")

    def execute(self, path: str = "", max_lines: int = 100, **kwargs: Any) -> CapabilityResult:
        raw_path = path or kwargs.get("file")
        if not raw_path:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Path parameter is required.",
                message="Cannot read: no file path specified.",
            )
        target = Path(raw_path).expanduser().resolve()
        if not target.exists():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"File does not exist: {target}",
                message=f"File '{raw_path}' does not exist.",
            )
        if target.is_dir():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Path is a directory: {target}",
                message=f"Path '{raw_path}' is a directory, not a file.",
            )
        try:
            lines = []
            with open(target, "r", encoding=kwargs.get("encoding", "utf-8"), errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    lines.append(line)
            content = "".join(lines)
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"path": str(target), "content": content, "lines_read": len(lines)},
                message=f"Read {len(lines)} line(s) from {target.name}.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to read '{target}': {err}",
            )


class SearchFileContentCapability(BaseCapability):
    """Search inside documents and text files for specific keywords or text."""

    name = "filesystem.search_content"
    description = "Search inside documents and text files for specific keywords or text."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text query or keyword to search for inside files."},
            "directory": {"type": "string", "description": "Directory to search within (default '.').", "default": "."},
            "extension": {"type": "string", "description": "Optional file extension to filter by (e.g. 'txt', 'md', 'py')."},
            "limit": {"type": "integer", "description": "Maximum number of matching files (default 10).", "default": 10},
        },
        "required": ["query"],
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = False
    supports_observation = True
    tags = ("filesystem", "grep", "search", "content")

    def execute(
        self,
        query: str = "",
        directory: str = ".",
        extension: str | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> CapabilityResult:
        search_q = query or kwargs.get("pattern") or kwargs.get("text") or ""
        if not search_q:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Query parameter is required.",
                message="Cannot search: query string is required.",
            )
        target_dir = kwargs.get("path") or directory
        dir_path = Path(target_dir).expanduser().resolve()
        if not dir_path.exists() or not dir_path.is_dir():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Directory does not exist: {dir_path}",
                message=f"Directory '{target_dir}' does not exist.",
            )

        norm_ext = extension.lower().lstrip(".") if extension else None
        q_lower = search_q.lower()
        limit = max(1, min(50, int(limit)))
        matches = []
        skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".cache"}

        try:
            for root, dirs, files in os.walk(dir_path):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                current_depth = len(Path(root).parts) - len(dir_path.parts)
                if current_depth > 4:
                    dirs.clear()
                    continue

                for file_name in files:
                    if file_name.startswith("."):
                        continue
                    if norm_ext and not file_name.lower().endswith(f".{norm_ext}"):
                        continue

                    full_path = Path(root) / file_name
                    try:
                        if full_path.stat().st_size > 5 * 1024 * 1024:
                            continue  # skip files larger than 5MB
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            for line_idx, line in enumerate(f, 1):
                                if q_lower in line.lower():
                                    matches.append(
                                        {
                                            "name": file_name,
                                            "path": str(full_path),
                                            "line": line_idx,
                                            "preview": line.strip()[:120],
                                        }
                                    )
                                    break  # Record one match per file
                    except (OSError, PermissionError):
                        continue

                    if len(matches) >= limit:
                        break
                if len(matches) >= limit:
                    break

            if not matches:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"query": search_q, "matches": [], "count": 0},
                    message=f"No files found containing '{search_q}' in {dir_path.name}.",
                )

            summary = ", ".join(m["name"] for m in matches[:3])
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={
                    "query": search_q,
                    "matches": matches,
                    "count": len(matches),
                    "path": matches[0]["path"],
                    "file": matches[0]["path"],
                },
                message=f"Found {len(matches)} file(s) matching '{search_q}': {summary}.",
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Search failed: {err}",
            )


class FindDuplicateFilesCapability(BaseCapability):
    """Find duplicate files in a directory by size and SHA256 content hash."""

    name = "filesystem.find_duplicates"
    description = "Find duplicate files in a directory by comparing content hashes."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "directory": {"type": "string", "description": "Directory to scan (default '.').", "default": "."},
            "limit": {"type": "integer", "description": "Maximum duplicate groups to return.", "default": 20},
        },
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = False
    supports_observation = True
    tags = ("filesystem", "duplicates", "hash", "clean")

    def execute(self, directory: str = ".", limit: int = 20, **kwargs: Any) -> CapabilityResult:
        target_dir = kwargs.get("path") or directory
        dir_path = Path(target_dir).expanduser().resolve()
        if not dir_path.exists() or not dir_path.is_dir():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Directory does not exist: {dir_path}",
                message=f"Directory '{target_dir}' does not exist.",
            )

        limit = max(1, min(50, int(limit)))
        by_size: dict[int, list[Path]] = {}
        skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".cache"}

        try:
            for root, dirs, files in os.walk(dir_path):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for f in files:
                    if f.startswith("."):
                        continue
                    p = Path(root) / f
                    try:
                        sz = p.stat().st_size
                        if sz > 0:  # ignore empty files
                            by_size.setdefault(sz, []).append(p)
                    except (OSError, PermissionError):
                        continue

            duplicates: list[dict[str, Any]] = []
            for sz, paths in by_size.items():
                if len(paths) < 2:
                    continue
                # Hash files with identical size
                by_hash: dict[str, list[str]] = {}
                for p in paths:
                    try:
                        h = hashlib.sha256()
                        with open(p, "rb") as fh:
                            while chunk := fh.read(65536):
                                h.update(chunk)
                        by_hash.setdefault(h.hexdigest(), []).append(str(p))
                    except (OSError, PermissionError):
                        continue

                for h_val, dup_paths in by_hash.items():
                    if len(dup_paths) > 1:
                        duplicates.append(
                            {
                                "hash": h_val[:12],
                                "size_bytes": sz,
                                "size_formatted": format_bytes(sz),
                                "count": len(dup_paths),
                                "files": dup_paths,
                            }
                        )
                        if len(duplicates) >= limit:
                            break
                if len(duplicates) >= limit:
                    break

            if not duplicates:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"duplicates": [], "count": 0},
                    message=f"No duplicate files detected in {dir_path.name}.",
                )

            total_wasted = sum(d["size_bytes"] * (d["count"] - 1) for d in duplicates)
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"duplicates": duplicates, "count": len(duplicates), "wasted_bytes": total_wasted},
                message=f"Found {len(duplicates)} duplicate group(s) wasting {format_bytes(total_wasted)} in {dir_path.name}.",
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Duplicate search failed: {err}",
            )


class LargestFilesCapability(BaseCapability):
    """Find the largest files in a directory or home folder."""

    name = "filesystem.largest_files"
    description = "Find the largest files in a directory or home directory."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "directory": {"type": "string", "description": "Directory to inspect (default '.').", "default": "."},
            "limit": {"type": "integer", "description": "Number of top files to return (default 10).", "default": 10},
        },
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = False
    supports_observation = True
    tags = ("filesystem", "size", "disk", "large")

    def execute(self, directory: str = ".", limit: int = 10, **kwargs: Any) -> CapabilityResult:
        target_dir = kwargs.get("path") or directory
        dir_path = Path(target_dir).expanduser().resolve()
        if not dir_path.exists() or not dir_path.is_dir():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Directory does not exist: {dir_path}",
                message=f"Directory '{target_dir}' does not exist.",
            )

        limit = max(1, min(50, int(limit)))
        all_files = []
        skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".cache"}

        try:
            for root, dirs, files in os.walk(dir_path):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for f in files:
                    p = Path(root) / f
                    try:
                        sz = p.stat().st_size
                        all_files.append({"name": f, "path": str(p), "size_bytes": sz, "size_formatted": format_bytes(sz)})
                    except (OSError, PermissionError):
                        continue

            all_files.sort(key=lambda x: x["size_bytes"], reverse=True)
            top_files = all_files[:limit]

            if not top_files:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"files": [], "count": 0},
                    message=f"No files found in {dir_path.name}.",
                )

            summary = ", ".join(f"{f['name']} ({f['size_formatted']})" for f in top_files[:3])
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"files": top_files, "count": len(top_files)},
                message=f"Largest {len(top_files)} file(s) in {dir_path.name}: {summary}.",
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to find largest files: {err}",
            )


class OrganizeFilesCapability(BaseCapability):
    """Organize files in a folder into subdirectories based on file category or type."""

    name = "filesystem.organize"
    description = "Organize files in a folder into subdirectories based on file category or type."
    category = CapabilityCategory.FILESYSTEM
    input_schema = {
        "type": "object",
        "properties": {
            "source_dir": {"type": "string", "description": "Directory to organize (default '~/Downloads').", "default": "~/Downloads"},
            "category": {"type": "string", "description": "Category to organize (e.g. 'screenshots', 'images', 'documents', 'all').", "default": "all"},
            "destination_dir": {"type": "string", "description": "Optional destination directory."},
        },
    }
    risk_category = ActionCategory.FILESYSTEM_WRITE
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True
    tags = ("filesystem", "organize", "sort", "move")

    CATEGORY_MAP = {
        "screenshots": {
            "extensions": {".png", ".jpg", ".jpeg", ".webp"},
            "name_pattern": "screenshot",
            "default_subfolder": "Screenshots",
        },
        "images": {
            "extensions": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"},
            "default_subfolder": "Images",
        },
        "documents": {
            "extensions": {".pdf", ".docx", ".doc", ".txt", ".odt", ".epub", ".rtf", ".pptx", ".xlsx", ".csv"},
            "default_subfolder": "Documents",
        },
        "audio": {
            "extensions": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"},
            "default_subfolder": "Audio",
        },
        "video": {
            "extensions": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
            "default_subfolder": "Videos",
        },
        "archives": {
            "extensions": {".zip", ".tar", ".gz", ".tar.gz", ".bz2", ".7z", ".rar"},
            "default_subfolder": "Archives",
        },
        "code": {
            "extensions": {".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".html", ".css", ".json", ".yaml", ".sh"},
            "default_subfolder": "Code",
        },
    }

    def execute(
        self,
        source_dir: str = "~/Downloads",
        category: str = "all",
        destination_dir: str | None = None,
        **kwargs: Any,
    ) -> CapabilityResult:
        src = Path(source_dir).expanduser().resolve()
        if not src.exists() or not src.is_dir():
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=f"Source directory does not exist: {src}",
                message=f"Directory '{source_dir}' does not exist.",
            )

        cat_clean = category.strip().lower()
        moved_records = []

        try:
            # Determine files in source dir (flat, top-level files only)
            files = [p for p in src.iterdir() if p.is_file() and not p.name.startswith(".")]

            for file_path in files:
                ext = file_path.suffix.lower()
                dest_subfolder = None

                if cat_clean == "screenshots" or (cat_clean == "all" and "screenshot" in file_path.name.lower()):
                    if ext in self.CATEGORY_MAP["screenshots"]["extensions"] or "screenshot" in file_path.name.lower():
                        dest_subfolder = destination_dir or str(Path("~/Pictures/Screenshots").expanduser())
                elif cat_clean in self.CATEGORY_MAP:
                    spec = self.CATEGORY_MAP[cat_clean]
                    if ext in spec["extensions"]:
                        dest_subfolder = destination_dir or str(src / spec["default_subfolder"])
                elif cat_clean == "all":
                    # Match against all categories
                    for cat_name, spec in self.CATEGORY_MAP.items():
                        if ext in spec["extensions"]:
                            dest_subfolder = str(src / spec["default_subfolder"])
                            break

                if dest_subfolder:
                    dest_path = Path(dest_subfolder).expanduser().resolve()
                    dest_path.mkdir(parents=True, exist_ok=True)
                    target_file = dest_path / file_path.name
                    # If target exists, add numerical suffix
                    if target_file.exists():
                        stem = file_path.stem
                        counter = 1
                        while (dest_path / f"{stem}_{counter}{ext}").exists():
                            counter += 1
                        target_file = dest_path / f"{stem}_{counter}{ext}"
                    shutil.move(str(file_path), str(target_file))
                    moved_records.append({"source": str(file_path), "destination": str(target_file), "name": file_path.name})

            count = len(moved_records)
            if count == 0:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"moved_count": 0, "files": []},
                    message=f"No matching files found in {src.name} to organize.",
                )

            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"moved_count": count, "files": moved_records},
                message=f"Organized and moved {count} file(s) in {src.name}.",
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to organize files: {err}",
            )
