"""AVI Desktop UI Application — GTK4 application wrapper."""

import os
import subprocess
import sys
from typing import Any

from avi.ui.detector import diagnose_gtk_environment
from avi.ui.window import CSS_STYLE, AviWindow, _GTK_AVAILABLE, check_display


class AviApp:
    """
    Wraps a Gtk.Application with AVI router, orchestrator, and config.

    Usage::

        from avi.ui.app import AviApp
        AviApp(router, config).run()
    """

    def __init__(self, router: Any, config: Any, orchestrator: Any | None = None) -> None:
        self.router = router
        self.config = config
        self.orchestrator = orchestrator

    def run(self, allow_system_fallback: bool = False) -> int:
        """Start the GTK4 event loop. Returns exit code."""
        if not _GTK_AVAILABLE:
            report = diagnose_gtk_environment()
            if allow_system_fallback and report.system_python_has_gtk4 and report.system_python_path:
                # Delegate to the system python which has GTK4 bindings
                cmd = [report.system_python_path, "-m", "avi.cli", "ui"]
                env = dict(os.environ)
                # Ensure src is on pythonpath if not globally installed
                src_dir = str(report.python_executable)
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
        from gi.repository import Gdk, GLib, Gtk

        app = Gtk.Application(application_id="io.github.banisher2005.avi")

        def _on_activate(gtk_app: Gtk.Application) -> None:
            # Apply CSS styling
            css_provider = Gtk.CssProvider()
            css_provider.load_from_data(CSS_STYLE)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            AviWindow(gtk_app, self.router, self.config, orchestrator=self.orchestrator)

        app.connect("activate", _on_activate)
        return app.run(None)
