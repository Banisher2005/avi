"""Keyboard input capabilities for AVI Agent Runtime."""

import ctypes
import ctypes.util
import logging
import re
import shutil
import subprocess
import time
from typing import Any

from avi.capabilities.desktop.clipboard import _write_system_clipboard
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger("avi.capabilities.desktop.input")

KEY_NAME_MAP: dict[str, str] = {
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "BackSpace",
    "tab": "Tab",
    "space": "space",
    "ctrl": "Control_L",
    "control": "Control_L",
    "alt": "Alt_L",
    "shift": "Shift_L",
    "super": "Super_L",
    "win": "Super_L",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "home": "Home",
    "end": "End",
    "del": "Delete",
    "delete": "Delete",
}


class X11InputBackend:
    """Native X11 / XWayland input synthesis using libXtst and libX11."""

    def __init__(self) -> None:
        self._available = False
        try:
            x11_path = ctypes.util.find_library("X11") or "libX11.so.6"
            xtst_path = ctypes.util.find_library("Xtst") or "libXtst.so.6"
            self.x11 = ctypes.cdll.LoadLibrary(x11_path)
            self.xtst = ctypes.cdll.LoadLibrary(xtst_path)

            self.x11.XOpenDisplay.restype = ctypes.c_void_p
            self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]

            self.x11.XCloseDisplay.restype = ctypes.c_int
            self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

            self.x11.XFlush.restype = ctypes.c_int
            self.x11.XFlush.argtypes = [ctypes.c_void_p]

            self.x11.XStringToKeysym.restype = ctypes.c_ulong
            self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]

            self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
            self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

            self.xtst.XTestFakeKeyEvent.restype = ctypes.c_int
            self.xtst.XTestFakeKeyEvent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_ulong,
            ]
            self._available = True
        except Exception as err:
            logger.debug("Failed to initialize native X11 input backend: %s", err)
            self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def _get_keycode(self, display: Any, key_name: str) -> int:
        canonical = KEY_NAME_MAP.get(key_name.lower(), key_name)
        keysym = self.x11.XStringToKeysym(canonical.encode("utf-8"))
        if not keysym and len(canonical) == 1:
            keysym = ord(canonical)
        if not keysym:
            return 0
        return int(self.x11.XKeysymToKeycode(display, keysym))

    def press_key_combination(self, keys: list[str]) -> bool:
        if not self._available:
            return False
        display = self.x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            keycodes: list[int] = []
            for k in keys:
                kc = self._get_keycode(display, k)
                if kc == 0:
                    logger.warning("Unknown keycode for key '%s'", k)
                    return False
                keycodes.append(kc)

            # Press all in sequence
            for kc in keycodes:
                self.xtst.XTestFakeKeyEvent(display, kc, 1, 0)
            self.x11.XFlush(display)
            time.sleep(0.02)

            # Release all in reverse order
            for kc in reversed(keycodes):
                self.xtst.XTestFakeKeyEvent(display, kc, 0, 0)
            self.x11.XFlush(display)
            return True
        finally:
            self.x11.XCloseDisplay(display)

    def type_ascii_text(self, text: str, delay: float = 0.012) -> bool:
        if not self._available:
            return False
        display = self.x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            shift_kc = self._get_keycode(display, "Shift_L")
            for ch in text:
                is_upper = ch.isupper() or ch in '~!@#$%^&*()_+{}|:"<>?'
                if is_upper and shift_kc:
                    self.xtst.XTestFakeKeyEvent(display, shift_kc, 1, 0)

                kc = self._get_keycode(display, ch.lower() if ch.isalpha() else ch)
                if kc != 0:
                    self.xtst.XTestFakeKeyEvent(display, kc, 1, 0)
                    self.xtst.XTestFakeKeyEvent(display, kc, 0, 0)

                if is_upper and shift_kc:
                    self.xtst.XTestFakeKeyEvent(display, shift_kc, 0, 0)

                self.x11.XFlush(display)
                if delay > 0:
                    time.sleep(delay)
            return True
        finally:
            self.x11.XCloseDisplay(display)


_x11_backend: X11InputBackend | None = None


def get_x11_backend() -> X11InputBackend:
    global _x11_backend
    if _x11_backend is None:
        _x11_backend = X11InputBackend()
    return _x11_backend


def parse_key_combo(combo: str) -> list[str]:
    """Split a key combination string like 'Ctrl+Alt+T' or 'ctrl+shift+p' into parts."""
    raw_keys = [k.strip() for k in re.split(r"[+_-]", combo) if k.strip()]
    return raw_keys or [combo.strip()]


def execute_press_key(key: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Synthesize key press or hotkey combo across available input backends."""
    keys = parse_key_combo(key)

    # 1. Try xdotool if installed
    if shutil.which("xdotool"):
        try:
            xdo_key = "+".join(keys)
            proc = subprocess.run(
                ["xdotool", "key", xdo_key],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, f"Sent key '{key}' via xdotool."
        except Exception as err:
            logger.debug("xdotool key error: %s", err)

    # 2. Try wtype if installed (Wayland)
    if shutil.which("wtype"):
        try:
            cmd = ["wtype"]
            for k in keys:
                cmd.extend(["-k", k])
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, f"Sent key '{key}' via wtype."
        except Exception as err:
            logger.debug("wtype key error: %s", err)

    # 3. Try native X11 / libXtst backend
    backend = get_x11_backend()
    if backend.is_available:
        if backend.press_key_combination(keys):
            return True, f"Sent key '{key}' via native X11 input."

    return False, f"Failed to synthesize key press '{key}': no working input backend available."


def execute_type_text(
    text: str,
    delay_ms: int = 12,
    use_clipboard: bool = False,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """Type string into focused desktop application."""
    if not text:
        return True, "Typed empty string."

    # If multiline or explicit clipboard requested, copy and synthesize ctrl+v
    if use_clipboard or "\n" in text or len(text) > 120 or not text.isascii():
        try:
            _write_system_clipboard(text)
            ok, msg = execute_press_key("ctrl+v", timeout=timeout)
            if ok:
                return True, f"Typed {len(text)} characters into active window via clipboard paste."
        except Exception as err:
            logger.debug("Clipboard paste fallback failed: %s", err)

    # 1. Try xdotool type
    if shutil.which("xdotool"):
        try:
            proc = subprocess.run(
                ["xdotool", "type", "--delay", str(max(1, delay_ms)), text],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, f"Typed {len(text)} characters via xdotool."
        except Exception as err:
            logger.debug("xdotool type error: %s", err)

    # 2. Try wtype (Wayland)
    if shutil.which("wtype"):
        try:
            proc = subprocess.run(
                ["wtype", text],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout,
            )
            if proc.returncode == 0:
                return True, f"Typed {len(text)} characters via wtype."
        except Exception as err:
            logger.debug("wtype text error: %s", err)

    # 3. Try native X11 backend
    backend = get_x11_backend()
    if backend.is_available and text.isascii():
        if backend.type_ascii_text(text, delay=max(0.001, delay_ms / 1000.0)):
            return True, f"Typed {len(text)} characters via native X11 input."

    # 4. Final attempt: clipboard paste
    try:
        _write_system_clipboard(text)
        ok, msg = execute_press_key("ctrl+v", timeout=timeout)
        if ok:
            return True, f"Typed {len(text)} characters via clipboard paste fallback."
    except Exception as err:
        logger.debug("Final clipboard fallback failed: %s", err)

    return False, f"Failed to type text: no working input backend available."


class TypeTextCapability(BaseCapability):
    """Type a string of text into the active desktop application."""

    name = "desktop.input.type_text"
    description = "Type text into the currently active or focused application window."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text string to type into the active window",
            },
            "delay_ms": {
                "type": "integer",
                "description": "Optional delay in milliseconds between keystrokes (default 12ms)",
            },
        },
        "required": ["text"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    tags = ("desktop", "input", "keyboard", "type", "text")

    def execute(self, **kwargs: Any) -> CapabilityResult:
        raw_text = kwargs.get("text")
        if raw_text is None:
            raw_text = kwargs.get("content") or kwargs.get("string") or kwargs.get("value")
        if raw_text is None:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Parameter 'text' is required to type text.",
            )

        text = str(raw_text)
        delay_ms = int(kwargs.get("delay_ms", 12))
        use_clipboard = bool(kwargs.get("use_clipboard", False))

        success, message = execute_type_text(text=text, delay_ms=delay_ms, use_clipboard=use_clipboard)
        if success:
            preview = text[:40] + "..." if len(text) > 40 else text
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=f"Typed {len(text)} characters: {preview!r}",
                data={"length": len(text), "preview": preview},
                classification=self.data_classification,
            )

        return CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error=message,
            message=message,
        )


class PressKeyCapability(BaseCapability):
    """Press a key or key combination in the active application."""

    name = "desktop.input.press_key"
    description = "Press a key or shortcut combination (e.g. 'Return', 'Escape', 'ctrl+w', 'alt+tab') in the active window."
    input_schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The key or key combination to press (e.g. 'Return', 'Escape', 'ctrl+c', 'ctrl+shift+p')",
            },
        },
        "required": ["key"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    tags = ("desktop", "input", "keyboard", "key", "press", "hotkey")

    def execute(self, **kwargs: Any) -> CapabilityResult:
        raw_key = kwargs.get("key") or kwargs.get("hotkey") or kwargs.get("combo") or kwargs.get("keys")
        if not raw_key:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Parameter 'key' is required to press a key.",
            )

        key_str = str(raw_key).strip()
        success, message = execute_press_key(key_str)
        if success:
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=f"Pressed key '{key_str}'.",
                data={"key": key_str},
                classification=self.data_classification,
            )

        return CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error=message,
            message=message,
        )
