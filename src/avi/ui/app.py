"""AVI Desktop UI Application — GTK4 application wrapper."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from avi.ui.detector import diagnose_gtk_environment
from avi.ui.window import _GTK_AVAILABLE, CSS_STYLE, AviWindow, check_display


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
    ) -> None:
        self.router = router
        self.config = config
        self.orchestrator = orchestrator
        self.background = background
        self.toggle = toggle
        self.show = show
        self.hide = hide
        self.window: Any | None = None
        self._is_held = False

    def run(self, allow_system_fallback: bool = False) -> int:
        """Start the GTK4 event loop. Returns exit code."""
        if not _GTK_AVAILABLE:
            report = diagnose_gtk_environment()
            if (
                allow_system_fallback
                and report.system_python_has_gtk4
                and report.system_python_path
            ):
                # Clean subprocess boundary to system Python which has PyGObject / GTK4 bindings
                src_path = str(Path(__file__).resolve().parent.parent.parent)
                env = dict(os.environ)
                existing_pythonpath = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = (
                    f"{src_path}:{existing_pythonpath}" if existing_pythonpath else src_path
                )
                forward_args = list(sys.argv[1:]) if len(sys.argv) > 1 else ["ui"]
                if forward_args and forward_args[0] in (
                    "activate",
                    "activaite",
                    "activte",
                    "actvate",
                ):
                    forward_args = ["ui", "--toggle"] + forward_args[1:]
                cmd = [report.system_python_path, "-m", "avi.cli"] + forward_args
                proc = subprocess.run(cmd, env=env)
                return proc.returncode

            sys.stderr.write(f"{report.diagnostic_message}\n")
            return 1

        if not check_display():
            sys.stderr.write(
                "Error: No display server detected (WAYLAND_DISPLAY or DISPLAY not set).\n"
                "AVI UI requires a running display session. Use 'avi' in a terminal instead.\n"
            )
            return 1

        # Import GTK here (only reached when _GTK_AVAILABLE is True)
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gio, Gtk

        app = Gtk.Application(
            application_id="io.github.banisher2005.avi",
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
            self.window = AviWindow(
                gtk_app, self.router, self.config, orchestrator=self.orchestrator
            )

            def _on_close_request(*_args: Any) -> bool:
                if self.window is not None:
                    self.window.hide_overlay()
                return True  # Intercept and prevent window destruction

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

            if "--hide" in args or self.hide:
                win.hide_overlay()
                return 0

            if any(arg in ("--background", "-b", "--daemon") for arg in args) or self.background:
                win.hide_overlay()
                return 0

            if "--toggle" in args or self.toggle:
                win.toggle_overlay()
                return 0

            win.show_overlay()
            return 0

        def _on_activate(gtk_app: Gtk.Application) -> None:
            win = _ensure_window(gtk_app)
            if win is not None and not self.background:
                win.show_overlay()

        app.connect("command-line", _on_command_line)
        app.connect("activate", _on_activate)

        app.hold()
        self._is_held = True

        return app.run(sys.argv)
