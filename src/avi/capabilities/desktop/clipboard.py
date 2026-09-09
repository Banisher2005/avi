"""Clipboard capabilities for AVI Agent Runtime."""

import logging
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

logger = logging.getLogger("avi.capabilities.desktop.clipboard")


def _read_system_clipboard(timeout: float = 3.0) -> str:
    """Read plain text from system clipboard using available Linux clipboard utilities."""
    # 1. Try wl-paste (native Wayland)
    if shutil.which("wl-paste"):
        try:
            proc = subprocess.run(
                ["wl-paste", "--no-newline"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception as err:
            logger.debug("wl-paste read failed: %s", err)

    # 2. Try xclip (X11 / XWayland)
    if shutil.which("xclip"):
        try:
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception as err:
            logger.debug("xclip read failed: %s", err)

    # 3. Try xsel (X11 / XWayland)
    if shutil.which("xsel"):
        try:
            proc = subprocess.run(
                ["xsel", "--clipboard", "--output"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception as err:
            logger.debug("xsel read failed: %s", err)

    raise RuntimeError("No supported clipboard utility found (tried wl-paste, xclip, xsel).")


def _write_system_clipboard(text: str, timeout: float = 3.0) -> None:
    """Write plain text to system clipboard using available Linux clipboard utilities."""
    # 1. Try wl-copy (native Wayland)
    if shutil.which("wl-copy"):
        try:
            proc = subprocess.run(
                ["wl-copy"],
                input=text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return
        except Exception as err:
            logger.debug("wl-copy write failed: %s", err)

    # 2. Try xclip (X11 / XWayland)
    if shutil.which("xclip"):
        try:
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return
        except Exception as err:
            logger.debug("xclip write failed: %s", err)

    # 3. Try xsel (X11 / XWayland)
    if shutil.which("xsel"):
        try:
            proc = subprocess.run(
                ["xsel", "--clipboard", "--input"],
                input=text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return
        except Exception as err:
            logger.debug("xsel write failed: %s", err)

    raise RuntimeError("No supported clipboard utility found (tried wl-copy, xclip, xsel).")


class ClipboardGetCapability(BaseCapability):
    """Read the current text content from the system clipboard."""

    name = "desktop.clipboard.get"
    description = "Read the current text content from the system clipboard."
    input_schema = {
        "type": "object",
        "properties": {},
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    tags = ("clipboard", "read", "get", "text", "desktop")

    def execute(self, **kwargs: Any) -> CapabilityResult:
        try:
            content = _read_system_clipboard()
            preview = content[:60] + "..." if len(content) > 60 else content
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=f"Clipboard content ({len(content)} chars): {preview!r}",
                data={"text": content, "length": len(content)},
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to read clipboard: {err}",
            )


class ClipboardSetCapability(BaseCapability):
    """Copy text content to the system clipboard."""

    name = "desktop.clipboard.set"
    description = "Copy specified text content to the system clipboard."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text string to copy to the clipboard",
            },
        },
        "required": ["text"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    tags = ("clipboard", "write", "set", "copy", "text", "desktop")

    def execute(self, **kwargs: Any) -> CapabilityResult:
        raw_text = kwargs.get("text")
        if raw_text is None:
            raw_text = kwargs.get("content") or kwargs.get("value")
        if raw_text is None:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Parameter 'text' is required to set clipboard content.",
            )

        text = str(raw_text)
        max_len = 500_000
        if len(text) > max_len:
            text = text[:max_len]

        try:
            _write_system_clipboard(text)
            preview = text[:60] + "..." if len(text) > 60 else text
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=f"Copied to clipboard ({len(text)} chars): {preview!r}",
                data={"text": text, "length": len(text)},
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to write clipboard: {err}",
            )
