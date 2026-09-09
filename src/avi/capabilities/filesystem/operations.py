"""Safe filesystem mutating capabilities with strict risk classification."""

import shutil
from pathlib import Path
from typing import Any

from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory


class CreateDirectoryCapability(BaseCapability):
    """Creates a directory at the specified path."""

    name = "filesystem.create_directory"
    description = "Create a new folder or directory path."
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
