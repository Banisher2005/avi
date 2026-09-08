"""AVI Desktop UI Application — GTK4 application wrapper.

Daemon / IPC architecture
--------------------------
AVI UI runs as a single resident GTK4 daemon.  All subsequent CLI invocations
talk to the already-running daemon via D-Bus (org.gtk.Application CommandLine).

Lifecycle:
  avi ui                 → foreground mode (stays in terminal, Ctrl-C to exit)
  avi ui --background    → daemonise: start daemon hidden, return immediately
  avi ui --show          → IPC: show overlay (start daemon first if not running)
  avi ui --hide          → IPC: hide overlay
  avi ui --toggle        → IPC: toggle overlay visibility
  avi ui --quit          → IPC: terminate daemon

Venv / system-Python split
---------------------------
The venv does NOT contain PyGObject.  When GTK is unavailable in the current
Python, the module re-launches itself under the system Python that *does* have
GTK4, forwarding all arguments.  For --background this is done with Popen
(non-blocking + new session) so the terminal is returned immediately.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from avi.ui.detector import diagnose_gtk_environment
from avi.ui.window import _GTK_AVAILABLE, CSS_STYLE, AviWindow, check_display

# D-Bus name used for single-instance enforcement
_APP_ID = "io.github.banisher2005.avi"
# Path used by GApplication on the session bus
_DBUS_PATH = "/io/github/banisher2005/avi"


# ---------------------------------------------------------------------------
# Helpers — D-Bus / daemon checks (no GTK required)
# ---------------------------------------------------------------------------


def _daemon_is_running() -> bool:
    """Return True if an AVI daemon is already registered on the session D-Bus."""
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return False
    try:
        result = subprocess.run(
            [
                gdbus,
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.ListNames",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return _APP_ID in result.stdout
    except Exception:
        return False


def _send_ipc(flag: str) -> int:
    """Send an IPC command to the running daemon via gdbus.  Returns exit code.

    Uses org.gtk.Application.CommandLine D-Bus method.
    Signature: (o objectpath, aay args, a{sv} platform-data) → (i exit-code)
    """
    gdbus = shutil.which("gdbus")
    if not gdbus:
        sys.stderr.write("gdbus not found – cannot send IPC command.\n")
        return 1
    # Build each argument separately (gdbus expects positional params, not a single tuple)
    argv_items = ["avi", "ui", flag]
    # Each string as a null-terminated byte array GVariant literal [97, 118, 105, 0]
    aay_parts = [
        "[" + ", ".join(str(b) for b in (s.encode() + b"\x00")) + "]"
        for s in argv_items
    ]
    arg_o = "/test/ipc/1"
    arg_aay = "[" + ", ".join(aay_parts) + "]"
    arg_dict = "{}"
    try:
        result = subprocess.run(
            [
                gdbus,
                "call",
                "--session",
                "--dest",
                _APP_ID,
                "--object-path",
                _DBUS_PATH,
                "--method",
                "org.gtk.Application.CommandLine",
                arg_o,
                arg_aay,
                arg_dict,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode
    except Exception as exc:
        sys.stderr.write(f"IPC error: {exc}\n")
        return 1


def _start_daemon(system_python: str, src_path: str, env: dict) -> None:
    """Launch the GTK daemon as a detached background process."""
    cmd = [system_python, "-m", "avi.cli", "ui", "--background", "--_daemon-inner"]
    subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach from terminal → survives shell exit
    )


# ---------------------------------------------------------------------------
# Main application class
# ---------------------------------------------------------------------------


class AviApp:
    """
    Wraps a Gtk.Application with AVI router, orchestrator, and config.
    Manages resident background/daemon mode and single-instance command dispatch.

    Usage::

        from avi.ui.app import AviApp
        AviApp(router, config).run()
    """

    def __init__(
        self,
        router: Any,
        config: Any,
        orchestrator: Any | None = None,
        background: bool = False,
        toggle: bool = False,
        show: bool = False,
        hide: bool = False,
        quit_flag: bool = False,
        daemon_inner: bool = False,
    ) -> None:
        self.router = router
        self.config = config
        self.orchestrator = orchestrator
        self.background = background
        self.toggle = toggle
        self.show = show
        self.hide = hide
        self.quit_flag = quit_flag
        # daemon_inner: set when this IS the actual GTK daemon process (not a
        # thin shim launching it); controls whether we stay resident.
        self.daemon_inner = daemon_inner
        self.window: Any | None = None
        self._is_held = False

    # -----------------------------------------------------------------------
    # Venv → system-Python boundary helpers
    # -----------------------------------------------------------------------

    def _build_env(self) -> tuple[str, dict]:
        """Return (src_path, env) for launching system Python subprocess."""
        src_path = str(Path(__file__).resolve().parent.parent.parent)
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path
        return src_path, env

    def _forward_args(self) -> list[str]:
        """Build forward_args from sys.argv, normalising activate typos."""
        forward_args = list(sys.argv[1:]) if len(sys.argv) > 1 else ["ui"]
        if forward_args and forward_args[0] in (
            "activate",
            "activaite",
            "activte",
            "actvate",
        ):
            forward_args = ["ui", "--toggle"] + forward_args[1:]
        return forward_args

    # -----------------------------------------------------------------------
    # Public run() entry point
    # -----------------------------------------------------------------------

    def run(self, allow_system_fallback: bool = False) -> int:
        """Start the GTK4 event loop (or delegate to system Python). Returns exit code."""
        if not _GTK_AVAILABLE:
            return self._run_venv_shim(allow_system_fallback)

        if not check_display():
            sys.stderr.write(
                "Error: No display server detected (WAYLAND_DISPLAY or DISPLAY not set).\n"
                "AVI UI requires a running display session. Use 'avi' in a terminal instead.\n"
            )
            return 1

        return self._run_gtk()

    # -----------------------------------------------------------------------
    # Venv shim — no GTK available here
    # -----------------------------------------------------------------------

    def _run_venv_shim(self, allow_system_fallback: bool) -> int:
        """Handle the case where we're inside the venv (no PyGObject)."""
        report = diagnose_gtk_environment()
        if not (
            allow_system_fallback
            and report.system_python_has_gtk4
            and report.system_python_path
        ):
            sys.stderr.write(f"{report.diagnostic_message}\n")
            return 1

        src_path, env = self._build_env()
        sys_py = report.system_python_path

        # ── If daemon is already running, route via D-Bus IPC directly ───────
        if _daemon_is_running():
            if self.quit_flag:
                return _send_ipc("--quit")
            if self.hide:
                return _send_ipc("--hide")
            if self.background:
                sys.stdout.write("AVI overlay daemon is already running.\n")
                return 0
            flag = "--toggle" if self.toggle else "--show"
            return _send_ipc(flag)

        # ── No daemon running: handle quit / hide / auto-start ───────────────
        if self.quit_flag:
            sys.stdout.write("AVI overlay is not running.\n")
            return 0

        if self.hide:
            return 0  # not running, so already hidden

        # Auto-start daemon on show or toggle
        if self.show or self.toggle:
            flag = "--show" if self.show else "--toggle"
            _start_daemon(sys_py, src_path, env)
            for _ in range(30):
                time.sleep(0.1)
                if _daemon_is_running():
                    break
            return _send_ipc(flag)

        # Background / daemon mode: start resident daemon and return immediately
        if self.background:
            _start_daemon(sys_py, src_path, env)
            sys.stdout.write("AVI overlay daemon started in background.\n")
            return 0

        # ── Foreground mode (avi ui with no flags and no daemon running) ────
        forward_args = self._forward_args()
        cmd = [sys_py, "-m", "avi.cli"] + forward_args
        proc = subprocess.run(cmd, env=env)
        return proc.returncode

    # -----------------------------------------------------------------------
    # GTK event loop — only reached under system Python with PyGObject
    # -----------------------------------------------------------------------

    def _run_gtk(self) -> int:
        """Build and run the GTK4 Application event loop.

        When a daemon is already running and this is a client command, route via
        D-Bus IPC immediately.
        """
        # ── If daemon is running and we are not the daemon inner process ────
        if _daemon_is_running() and not self.daemon_inner:
            if self.quit_flag:
                return _send_ipc("--quit")
            if self.hide:
                return _send_ipc("--hide")
            if self.background:
                sys.stdout.write("AVI overlay daemon is already running.\n")
                return 0
            flag = "--toggle" if self.toggle else "--show"
            return _send_ipc(flag)

        # --background without --_daemon-inner: spawn the actual daemon
        if self.background and not self.daemon_inner:
            src_path, env = self._build_env()
            _start_daemon(sys.executable, src_path, env)
            sys.stdout.write("AVI overlay daemon started in background.\n")
            return 0

        # --quit: send to daemon or report not running
        if self.quit_flag:
            sys.stdout.write("AVI overlay is not running.\n")
            return 0

        # --hide: already hidden if no daemon
        if self.hide:
            return 0

        # --show / --toggle: daemon not running, start it then send
        if (self.show or self.toggle) and not self.daemon_inner:
            flag = "--show" if self.show else "--toggle"
            src_path, env = self._build_env()
            _start_daemon(sys.executable, src_path, env)
            for _ in range(30):
                time.sleep(0.1)
                if _daemon_is_running():
                    break
            return _send_ipc(flag)

        # ── Normal GTK event loop (foreground or daemon-inner) ───────────────
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gio, GLib, Gtk

        app = Gtk.Application(
            application_id=_APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )

        def _ensure_window(gtk_app: Gtk.Application) -> Any:
            if self.window is not None:
                return self.window

            display = Gdk.Display.get_default()
            if display is None:
                sys.stderr.write("Error: Could not connect to display server.\n")
                gtk_app.quit()
                return None

            # Apply CSS styling
            css_provider = Gtk.CssProvider()
            css_provider.load_from_data(CSS_STYLE)
            Gtk.StyleContext.add_provider_for_display(
                display,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            is_initially_hidden = bool(self.background or self.daemon_inner)
            self.window = AviWindow(
                gtk_app,
                self.router,
                self.config,
                orchestrator=self.orchestrator,
                visible=not is_initially_hidden,
            )

            def _on_close_request(*_args: Any) -> bool:
                if self.daemon_inner:
                    if self.window is not None:
                        self.window.hide_overlay()
                    return True  # prevent destruction in daemon mode; keep window for re-use
                # Foreground standalone mode: release hold and quit cleanly
                if self.window is not None:
                    self.window.hide_overlay()
                if self._is_held:
                    gtk_app.release()
                    self._is_held = False
                gtk_app.quit()
                return False

            if hasattr(self.window, "window") and self.window.window is not None:
                self.window.window.connect("close-request", _on_close_request)

            return self.window

        def _on_command_line(gtk_app: Gtk.Application, cmdline: Any) -> int:
            args = list(cmdline.get_arguments())
            win = _ensure_window(gtk_app)
            if win is None:
                return 1

            if any(arg in ("--quit", "-q") for arg in args):
                if self._is_held:
                    gtk_app.release()
                    self._is_held = False
                gtk_app.quit()
                return 0

            if "--hide" in args:
                win.hide_overlay()
                return 0

            if any(arg in ("--background", "-b", "--daemon", "--_daemon-inner") for arg in args):
                # Stay resident but hidden
                win.hide_overlay()
                return 0

            if "--toggle" in args:
                win.toggle_overlay()
                return 0

            # --show or plain "ui" → present overlay
            win.show_overlay()
            return 0

        def _on_activate(gtk_app: Gtk.Application) -> None:
            win = _ensure_window(gtk_app)
            if win is not None and not (self.background or self.daemon_inner):
                win.show_overlay()

        app.connect("command-line", _on_command_line)
        app.connect("activate", _on_activate)

        # Only hold the application resident if this process is a background daemon
        if self.daemon_inner or self.background:
            app.hold()
            self._is_held = True

        return app.run(sys.argv)
