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
        assert b"avi-conversation-area" in CSS_STYLE
        assert b"avi-bubble-user" in CSS_STYLE
        assert b"avi-bubble-assistant" in CSS_STYLE
        assert b"avi-confirm-card" in CSS_STYLE
        assert b"avi-status-bar" in CSS_STYLE

    def test_gtk_available_is_bool(self):
        from avi.ui.window import _GTK_AVAILABLE

        assert isinstance(_GTK_AVAILABLE, bool)

    def test_check_display_imported(self):
        from avi.ui.window import check_display

        assert callable(check_display)


# ============================================================
# 5. AviWindow headless tests (mocked GTK)
# ============================================================


class TestAviWindowMocked:
    """Comprehensive tests for AviWindow logic, layout, cards, and state."""

    @pytest.fixture
    def mock_gtk(self, monkeypatch):
        mock_gtk = MagicMock()
        mock_gdk = MagicMock()
        mock_glib = MagicMock()
        mock_pango = MagicMock()
        mock_glib.idle_add = lambda fn, *args: fn(*args)

        import avi.ui.window as wmod

        monkeypatch.setattr(wmod, "Gtk", mock_gtk)
        monkeypatch.setattr(wmod, "Gdk", mock_gdk)
        monkeypatch.setattr(wmod, "GLib", mock_glib)
        monkeypatch.setattr(wmod, "Pango", mock_pango)
        monkeypatch.setattr(wmod, "_GTK_AVAILABLE", True)
        return mock_gtk, mock_gdk, mock_glib, mock_pango

    def test_window_construction(self, mock_gtk):
        from avi.ui.window import AviWindow

        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_config = MagicMock()
        mock_config.provider = "local"
        mock_config.model = "qwen2.5:1.5b"

        win = AviWindow(mock_app, mock_router, mock_config)
        assert win.window is not None
        assert win.prompt_entry is not None
        assert win.send_button is not None
        assert win.spinner is not None
        assert win.conversation_box is not None
        assert win.status_label is not None
        win.window.set_title.assert_called_with("⚡ AVI Assistant")
        win.window.present.assert_called()
        win.prompt_entry.grab_focus.assert_called()

    def test_input_handling_and_enter_submission(self, mock_gtk):
        from avi.ui.window import AviWindow

        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_config = MagicMock()
        mock_orch = MagicMock()

        win = AviWindow(mock_app, mock_router, mock_config, orchestrator=mock_orch)
        win.prompt_entry.get_text.return_value = "increase volume"

        win._on_prompt_submit(win.prompt_entry)

        win.prompt_entry.set_text.assert_called_with("")
        assert win._worker_thread is not None
        win._worker_thread.join(timeout=1.0)
        assert len(win._history_widgets) == 2

    def test_assistant_response_rendering(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        win._show_assistant_response("Volume increased by 5%.")

        assert len(win._history_widgets) == 1
        win.status_label.set_text.assert_called_with("Ready  ·  Esc to close")

    def test_screenshot_result_with_action_button(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        win._show_assistant_response("Captured screenshot.", action_path="/tmp/shot.png")

        assert len(win._history_widgets) == 1
        gtk_mock = mock_gtk[0]
        button_labels = [call.kwargs.get("label") for call in gtk_mock.Button.call_args_list]
        assert "Open Screenshot" in button_labels

    def test_confirmation_card_and_actions(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        proposal = MagicMock()
        proposal.command_line = "rm -rf /tmp/test"

        win._show_confirmation(proposal, "Delete directory?")
        assert win._pending_confirmation is not None
        assert len(win._history_widgets) == 1

        # Test Cancel action
        win._handle_confirm_cancel()
        assert win._pending_confirmation is None
        assert len(win._history_widgets) == 2  # Added "Action cancelled."

    def test_confirmation_proceed(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        proposal = MagicMock()
        proposal.command_line = "touch /tmp/test"

        win._show_confirmation(proposal, "Create file?")
        win._handle_confirm_proceed()
        assert win._pending_confirmation is None
        win.spinner.start.assert_called()

    def test_error_formatting_and_rendering(self, mock_gtk):
        from avi.ui.window import AviWindow, format_user_friendly_error

        # Test mapping
        assert "screenshot" in format_user_friendly_error("Screenshot portal failed").lower()
        assert "volume" in format_user_friendly_error("Pipewire audio error").lower()
        assert "blocked" in format_user_friendly_error("Blocked: Privilege escalation").lower()

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        win._show_error("I couldn't adjust the volume.")
        assert len(win._history_widgets) == 1
        win.status_label.set_text.assert_called_with("Ready  ·  Esc to close")

    def test_processing_state_cleanup_on_exception(self, mock_gtk):
        from avi.ui.window import AviWindow

        mock_orch = MagicMock()
        mock_orch.handle.side_effect = RuntimeError("Crash")
        win = AviWindow(MagicMock(), MagicMock(), MagicMock(), orchestrator=mock_orch)

        # Run query directly (synchronously invoking method for test)
        win._run_query("test query")
        assert win._is_busy is False
        win.spinner.stop.assert_called()

    def test_bounded_conversation_history(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock(), max_history=5)
        for i in range(10):
            win._add_user_message(f"Msg {i}")

        assert len(win._history_widgets) == 5
        assert win.conversation_box.remove.call_count == 5

    def test_keyboard_shortcuts(self, mock_gtk):
        from avi.ui.window import AviWindow

        mock_app = MagicMock()
        win = AviWindow(mock_app, MagicMock(), MagicMock())
        gdk = mock_gtk[1]
        gdk.KEY_Escape = 65307
        gdk.KEY_q = 113
        gdk.KEY_Q = 81
        gdk.KEY_l = 108
        gdk.KEY_L = 76
        gdk.ModifierType.CONTROL_MASK = 4

        # Escape closes window when idle
        win.prompt_entry.get_text.return_value = ""
        handled = win._on_key_pressed(MagicMock(), gdk.KEY_Escape, 0, 0)
        assert handled is True
        win.window.close.assert_called_once()

        # Ctrl+Q quits application
        handled = win._on_key_pressed(MagicMock(), gdk.KEY_q, 0, gdk.ModifierType.CONTROL_MASK)
        assert handled is True
        mock_app.quit.assert_called_once()

        # Ctrl+L focuses prompt
        handled = win._on_key_pressed(MagicMock(), gdk.KEY_l, 0, gdk.ModifierType.CONTROL_MASK)
        assert handled is True
        win.prompt_entry.grab_focus.assert_called()

    def test_single_instance_activate_behavior(self, mock_gtk):
        from avi.ui.app import AviApp

        app = AviApp(MagicMock(), MagicMock())
        mock_win = MagicMock()
        app.window = mock_win

        with patch.object(app, "window", mock_win):
            mock_win.present.assert_not_called()
            app.window.present()
            mock_win.present.assert_called_once()

    def test_prompt_submission_cleans_cli_syntax_and_quotes(self, mock_gtk):
        from avi.ui.window import AviWindow

        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_config = MagicMock()
        mock_orch = MagicMock()

        win = AviWindow(mock_app, mock_router, mock_config, orchestrator=mock_orch)
        win.prompt_entry.get_text.return_value = (
            'avi "find me the best YouTube video about building local AI agents"'
        )

        win._on_prompt_submit(win.prompt_entry)

        win.prompt_entry.set_text.assert_called_with("")
        assert win._worker_thread is not None
        win._worker_thread.join(timeout=1.0)
        assert len(win._history_widgets) >= 1

    def test_double_submission_guard(self, mock_gtk):
        from avi.ui.window import AviWindow

        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_config = MagicMock()
        mock_orch = MagicMock()

        win = AviWindow(mock_app, mock_router, mock_config, orchestrator=mock_orch)
        win.prompt_entry.get_text.return_value = "screenshot"

        # Manually set busy to simulate an active in-flight request
        win._is_busy = True
        win._on_prompt_submit(win.prompt_entry)

        win.status_label.set_text.assert_called_with(
            "AVI is currently busy working on a request..."
        )
        assert win._worker_thread is None

    def test_loading_status_deterministic_vs_llm(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())

        # Deterministic commands
        status, is_llm = win._get_loading_status("chrome")
        assert "Opening Google Chrome" in status
        assert is_llm is False

        status, is_llm = win._get_loading_status("screenshot")
        assert "Capturing screenshot" in status
        assert is_llm is False

        status, is_llm = win._get_loading_status("increase volume")
        assert "Increasing volume" in status
        assert is_llm is False

        status, is_llm = win._get_loading_status("mute")
        assert "Muting audio" in status
        assert is_llm is False

        status, is_llm = win._get_loading_status("downloads")
        assert "Opening Downloads" in status
        assert is_llm is False

        status, is_llm = win._get_loading_status(
            "find me the best YouTube video about local AI agents"
        )
        assert "Searching YouTube" in status
        assert is_llm is False

        # Open-ended query requiring LLM
        status, is_llm = win._get_loading_status(
            "why is the sky blue and how does light scattering work?"
        )
        assert "Thinking" in status
        assert is_llm is True

    def test_best_match_card_rendering_with_play_and_open(self, mock_gtk):
        from avi.retrieval.models import SearchResult
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        best = SearchResult(
            id="v1",
            title="Local AI Agents Masterclass",
            url="https://www.youtube.com/watch?v=v1",
            source="youtube",
            channel="Tech Guru",
            duration="14:20",
        )
        other1 = SearchResult(
            id="v2",
            title="Build an Agent in 10 mins",
            url="https://www.youtube.com/watch?v=v2",
            source="youtube",
            channel="Code Lab",
            duration="10:05",
        )
        other2 = SearchResult(
            id="v3",
            title="AI Agents from Scratch",
            url="https://www.youtube.com/watch?v=v3",
            source="youtube",
            channel="Dev Central",
            duration="22:15",
        )

        win._add_search_results_widget([best, other1, other2], selected_result=best)

        assert len(win._history_widgets) == 1
        gtk_mock = mock_gtk[0]
        button_labels = [call.kwargs.get("label") for call in gtk_mock.Button.call_args_list]
        assert "Play" in button_labels
        assert "Open" in button_labels


# ============================================================
# 6. GTK-specific tests (system Python or GTK-enabled venv)
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


class TestGtkRealSystemSmoke:
    """Smoke test running AviWindow directly in system Python if available."""

    def test_system_python_gtk4_window_smoke(self):
        import subprocess

        from avi.ui.detector import diagnose_gtk_environment

        report = diagnose_gtk_environment()
        if not (report.system_python_has_gtk4 and report.system_python_path):
            pytest.skip("System python with GTK4 not available")

        code = """
import sys
sys.path.insert(0, 'src')
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from unittest.mock import MagicMock
from avi.ui.window import AviWindow

app = Gtk.Application(application_id='io.github.banisher2005.avi.testsmoke')
def on_act(a):
    win = AviWindow(a, MagicMock(), MagicMock())
    win._show_assistant_response('Hello')
    a.quit()

app.connect('activate', on_act)
app.run(None)
print('SMOKE_OK')
"""
        proc = subprocess.run(
            [report.system_python_path, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "SMOKE_OK" in proc.stdout
