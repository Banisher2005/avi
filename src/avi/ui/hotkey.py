"""Configurable Global Hotkey subsystem with conflict detection and focus restoration."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import shutil
import subprocess
import threading
from typing import Any, Callable

logger = logging.getLogger("avi.ui.hotkey")

# Standard modifier bitmasks for X11
_SHIFT_MASK = 1 << 0
_CONTROL_MASK = 1 << 2
_MOD1_MASK = 1 << 3  # Alt
_MOD4_MASK = 1 << 6  # Super / Windows key
_ANY_MODIFIER = 1 << 15

# Common Keysyms
_KEYSYMS = {
    "space": 0x0020,
    "return": 0xFF0D,
    "enter": 0xFF0D,
    "escape": 0xFF1B,
    "tab": 0xFF09,
    "grave": 0x0060,
    "asciitilde": 0x007E,
}


def parse_hotkey_string(hotkey_str: str) -> tuple[int, int]:
    """Parse a shortcut description like '<Alt>space' or 'Alt+Space' into (modifiers, keysym)."""
    clean = hotkey_str.strip().replace("<", "").replace(">", " ").replace("+", " ")
    parts = [p.strip().lower() for p in clean.split() if p.strip()]

    modifiers = 0
    keysym = 0x0020  # default space

    for p in parts:
        if p in ("alt", "mod1"):
            modifiers |= _MOD1_MASK
        elif p in ("ctrl", "control"):
            modifiers |= _CONTROL_MASK
        elif p in ("shift",):
            modifiers |= _SHIFT_MASK
        elif p in ("super", "win", "mod4"):
            modifiers |= _MOD4_MASK
        elif p in _KEYSYMS:
            keysym = _KEYSYMS[p]
        elif len(p) == 1:
            keysym = ord(p)

    return modifiers, keysym


class HotkeyManager:
    """Manages system global hotkey listening, conflict detection, and previous window restoration."""

    def __init__(self, hotkey: str = "<Alt>space") -> None:
        self.hotkey = hotkey
        self.callback: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._previous_window_id: str | None = None
        self.conflict_detected = False
        self.is_supported = False
        self._display: Any = None
        self._x11: Any = None

    def record_active_window(self) -> None:
        """Capture the currently active window ID before summoning the command palette."""
        # Check xdotool
        xdotool = shutil.which("xdotool")
        if xdotool:
            try:
                proc = subprocess.run(
                    [xdotool, "getactivewindow"],
                    capture_output=True,
                    text=True,
                    timeout=0.3,
                )
                if proc.returncode == 0 and proc.stdout.strip().isdigit():
                    self._previous_window_id = proc.stdout.strip()
                    return
            except Exception:
                pass

        # Check wmctrl
        wmctrl = shutil.which("wmctrl")
        if wmctrl:
            try:
                proc = subprocess.run(
                    [wmctrl, "-a"],
                    capture_output=True,
                    text=True,
                    timeout=0.3,
                )
            except Exception:
                pass

    def restore_previous_window(self) -> bool:
        """Return focus to the previously active application when palette closes."""
        if not self._previous_window_id:
            return False

        target_id = self._previous_window_id
        self._previous_window_id = None

        xdotool = shutil.which("xdotool")
        if xdotool:
            try:
                subprocess.Popen(
                    [xdotool, "windowactivate", "--sync", target_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:
                pass

        wmctrl = shutil.which("wmctrl")
        if wmctrl:
            try:
                subprocess.Popen(
                    [wmctrl, "-i", "-a", target_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:
                pass

        return False

    def start(self, callback: Callable[[], None]) -> bool:
        """Start global hotkey listener thread if display server allows key grabbing."""
        self.callback = callback
        display_var = os.environ.get("DISPLAY")
        if not display_var:
            logger.debug("No DISPLAY variable found; global hotkey listener inactive.")
            return False

        lib_name = ctypes.util.find_library("X11")
        if not lib_name:
            logger.debug("libX11 not found; skipping direct X11 key grab.")
            return False

        try:
            x11 = ctypes.CDLL(lib_name)
            display = x11.XOpenDisplay(None)
            if not display:
                logger.debug("Failed to connect to X11 display for hotkey listener.")
                return False

            self._x11 = x11
            self._display = display
            self.is_supported = True
            self._running = True

            self._thread = threading.Thread(target=self._listener_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as exc:
            logger.warning("Could not initialize global hotkey listener: %s", exc)
            return False

    def _listener_loop(self) -> None:
        """Background thread executing X11 event loop for KeyPress events."""
        x11 = self._x11
        display = self._display
        root = x11.XDefaultRootWindow(display)

        modifiers, keysym = parse_hotkey_string(self.hotkey)
        keycode = x11.XKeysymToKeycode(display, keysym)

        if not keycode:
            logger.warning("Could not map keysym %s to keycode", keysym)
            return

        # Attempt to grab the key on root window.
        # Grab with standard lock masks (NumLock, CapsLock) to guarantee trigger.
        lock_masks = [0, 2, 16, 18]  # Lock combinations
        grab_success = True

        for mask in lock_masks:
            res = x11.XGrabKey(
                display,
                keycode,
                modifiers | mask,
                root,
                False,
                1,  # GrabModeAsync
                1,  # GrabModeAsync
            )
            if res != 0 and res != 1:
                grab_success = False

        if not grab_success:
            self.conflict_detected = True
            logger.info("Global hotkey '%s' might conflict with another application.", self.hotkey)

        class XEvent(ctypes.Structure):
            _fields_ = [("type", ctypes.c_int), ("pad", ctypes.c_byte * 192)]

        event = XEvent()

        try:
            while self._running:
                # XNextEvent blocks until an X event is delivered
                x11.XNextEvent(display, ctypes.byref(event))
                # KeyPress event type is 2 in X11 protocol
                if event.type == 2:
                    if self.callback:
                        try:
                            self.callback()
                        except Exception as cb_exc:
                            logger.error("Hotkey callback error: %s", cb_exc)
        except Exception as exc:
            logger.debug("Hotkey event loop exited: %s", exc)
        finally:
            try:
                x11.XCloseDisplay(display)
            except Exception:
                pass

    def stop(self) -> None:
        """Stop background hotkey listener."""
        self._running = False
