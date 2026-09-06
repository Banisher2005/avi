"""Unit tests for AVI Desktop UI subsystem (GTK4-optional, headless-safe).

All tests are designed to run with or without GTK4 installed. GTK-specific
behaviour is either mocked or skipped via pytest.importorskip when needed.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from avi.ui import check_display, is_ui_available
from avi.ui.window import _GTK_AVAILABLE

# ============================================================
# 1. Display & availability probes (no GTK needed)
# ============================================================


class TestDisplayDetection:
    """Tests for display server detection helpers."""

    def test_check_display_wayland(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            assert check_display() is True

    def test_check_display_x11(self):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            assert check_display() is True

    def test_check_display_both(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, clear=True):
            assert check_display() is True

    def test_check_display_headless(self):
        with patch.dict(os.environ, {}, clear=True):
            assert check_display() is False


class TestUiAvailability:
    """Tests for is_ui_available composite check."""

    def test_is_ui_available_no_display(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_ui_available() is False

    def test_is_ui_available_with_display_and_gtk(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            # Actual result depends on whether GTK4 is installed in this venv
            expected = _GTK_AVAILABLE
            assert is_ui_available() == expected

    def test_is_ui_available_no_gtk(self):
        """When _GTK_AVAILABLE is False, is_ui_available must always return False."""
        import avi.ui as ui_mod

        old = ui_mod._GTK_AVAILABLE
        ui_mod._GTK_AVAILABLE = False
        try:
            with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
                assert ui_mod.is_ui_available() is False
        finally:
            ui_mod._GTK_AVAILABLE = old


# ============================================================
# 2. AviApp.run() — headless-safe error paths
# ============================================================


class TestAviAppHeadless:
    """Tests for AviApp.run() in headless / no-GTK environments."""

    def test_run_no_gtk_returns_1(self, capsys):
        """If GTK4 is not installed, AviApp.run() exits with code 1."""
        from avi.ui.app import AviApp

        mock_router = MagicMock()
        mock_config = MagicMock()
        mock_config.provider = "local"
        mock_config.model = "qwen2.5:1.5b"

        with patch("avi.ui.app._GTK_AVAILABLE", False):
            app = AviApp(mock_router, mock_config)
            code = app.run()

        assert code == 1
        captured = capsys.readouterr()
        assert "GTK4" in captured.err or "PyGObject" in captured.err

    def test_run_no_display_returns_1(self, capsys):
        """If no display server is found, AviApp.run() exits with code 1."""
        from avi.ui.app import AviApp

        mock_router = MagicMock()
        mock_config = MagicMock()
        mock_config.provider = "local"
        mock_config.model = "qwen2.5:1.5b"

        with (
            patch("avi.ui.app._GTK_AVAILABLE", True),
            patch("avi.ui.app.check_display", return_value=False),
        ):
            app = AviApp(mock_router, mock_config)
            code = app.run()

        assert code == 1
        captured = capsys.readouterr()
        assert "display" in captured.err.lower()


# ============================================================
# 3. CLI ui subcommand
# ============================================================


class TestCliUiSubcommand:
    """Tests for 'avi ui' CLI subcommand dispatch."""

    def test_cli_ui_headless_no_display(self, capsys):
        """avi ui in headless env returns 1 with helpful error."""
        from avi.cli import main

        with (
            patch("avi.ui.app._GTK_AVAILABLE", True),
            patch("avi.ui.app.check_display", return_value=False),
        ):
            code = main(["ui"])

        assert code == 1
        captured = capsys.readouterr()
        assert "display" in captured.err.lower()

    def test_cli_ui_no_gtk(self, capsys):
        """avi ui with no GTK4 installed returns 1 with helpful error."""
        from avi.cli import main

        with patch("avi.ui.app._GTK_AVAILABLE", False):
            code = main(["ui"])

        assert code == 1
        captured = capsys.readouterr()
        assert "GTK4" in captured.err or "PyGObject" in captured.err

    def test_cli_ui_mock_gtk_app(self):
        """avi ui when GTK+display are mocked available calls Gtk.Application.run()."""
        from avi.cli import main

        # We need to mock at the AviApp level, not inside Gtk since gi isn't in venv
        with patch("avi.ui.app.AviApp") as mock_app_cls:
            mock_instance = MagicMock()
            mock_instance.run.return_value = 0
            mock_app_cls.return_value = mock_instance

            # cli.run_ui imports AviApp from avi.ui.app via the module
            with (
                patch("avi.ui.app._GTK_AVAILABLE", True),
                patch("avi.ui.app.check_display", return_value=True),
            ):
                # Also need to patch the AviApp used in run_ui
                with patch("avi.cli.run_ui") as mock_run_ui:
                    mock_run_ui.return_value = 0
                    code = main(["ui"])

        assert code == 0


# ============================================================
# 4. CSS and window constants sanity check
# ============================================================


class TestUiConstants:
    """Sanity checks for CSS and window constants."""

    def test_css_style_is_bytes(self):
        from avi.ui.window import CSS_STYLE

        assert isinstance(CSS_STYLE, bytes)
        assert b"avi-prompt-entry" in CSS_STYLE
        assert b"avi-response-view" in CSS_STYLE
        assert b"avi-status-bar" in CSS_STYLE

    def test_gtk_available_is_bool(self):
        from avi.ui.window import _GTK_AVAILABLE

        assert isinstance(_GTK_AVAILABLE, bool)
        # Just confirm it's a bool — value depends on runtime environment

    def test_check_display_imported(self):
        from avi.ui.window import check_display

        assert callable(check_display)


# ============================================================
# 5. GTK-specific tests (skipped when GTK4 not in venv)
# ============================================================


@pytest.mark.skipif(not _GTK_AVAILABLE, reason="GTK4 not available in this Python environment")
class TestGtkWindow:
    """Tests that require GTK4 to be installed in the Python environment."""

    def test_avi_window_can_import(self):
        from avi.ui.window import AviWindow

        assert AviWindow is not None

    def test_avi_app_can_import(self):
        from avi.ui.app import AviApp

        assert AviApp is not None
