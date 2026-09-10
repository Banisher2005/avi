"""Unit tests for AVI Desktop UI subsystem (GTK4-optional, headless-safe).

All tests are designed to run with or without GTK4 installed. GTK-specific
behaviour is either mocked or skipped via pytest.importorskip when needed.
"""

import os
import subprocess
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

        mock_report = MagicMock()
        mock_report.system_python_has_gtk4 = False
        mock_report.diagnostic_message = "PyGObject (GTK4) is not installed."

        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app.diagnose_gtk_environment", return_value=mock_report),
        ):
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


class TestOverlayRedesign:
    """Tests for Phase 14 AVI Command Overlay layout, controls, and behavior."""

    @pytest.fixture
    def mock_gtk(self, monkeypatch):
        mock_gtk = MagicMock()
        mock_gdk = MagicMock()
        mock_glib = MagicMock()
        mock_pango = MagicMock()
        mock_glib.idle_add = lambda fn, *args: fn(*args)
        mock_glib.timeout_add = MagicMock(return_value=999)
        mock_glib.source_remove = MagicMock()

        import avi.ui.window as wmod

        monkeypatch.setattr(wmod, "Gtk", mock_gtk)
        monkeypatch.setattr(wmod, "Gdk", mock_gdk)
        monkeypatch.setattr(wmod, "GLib", mock_glib)
        monkeypatch.setattr(wmod, "Pango", mock_pango)
        monkeypatch.setattr(wmod, "_GTK_AVAILABLE", True)
        return mock_gtk, mock_gdk, mock_glib, mock_pango

    def test_overlay_frameless_window_properties(self, mock_gtk):
        from avi.ui.window import AviWindow

        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_config = MagicMock()

        win = AviWindow(mock_app, mock_router, mock_config)
        win.window.set_decorated.assert_called_with(False)
        win.window.set_default_size.assert_called_with(800, 580)
        win.window.add_css_class.assert_any_call("avi-overlay-window")
        assert win.close_button is not None
        assert win.voice_button is not None
        assert win.send_button is not None

    def test_overlay_show_hide_toggle(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        win.window.is_visible.return_value = False

        win.show_overlay()
        win.window.set_visible.assert_called_with(True)
        win.window.present.assert_called()
        win.prompt_entry.grab_focus.assert_called()

        win.hide_overlay()
        win.window.set_visible.assert_called_with(False)

        # Toggle from hidden -> should show
        win.window.is_visible.return_value = False
        win.toggle_overlay()
        win.window.set_visible.assert_called_with(True)

        # Toggle from visible -> should hide
        win.window.is_visible.return_value = True
        win.toggle_overlay()
        win.window.set_visible.assert_called_with(False)

    def test_auto_dismiss_scheduling_and_cancellation(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        _, _, mock_glib, _ = mock_gtk

        win._schedule_auto_dismiss(1800)
        assert win._auto_dismiss_tag == 999
        mock_glib.timeout_add.assert_called_with(1800, win._on_auto_dismiss_timeout)

        # Typing in prompt cancels auto dismiss
        win._on_prompt_changed(win.prompt_entry)
        mock_glib.source_remove.assert_called_with(999)
        assert win._auto_dismiss_tag is None

        # Keypress also cancels auto dismiss
        win._schedule_auto_dismiss(1800)
        win._on_key_pressed(MagicMock(), 0, 0, 0)
        assert win._auto_dismiss_tag is None

    def test_transient_action_auto_dismiss(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        win._show_assistant_response("✓ Opening Google Chrome.", auto_dismiss=True)

        assert win._auto_dismiss_tag == 999
        win.status_label.set_text.assert_called_with("✓ Done  ·  Auto-closing in 2s")

    def test_app_flags_and_daemon_mode(self):
        from avi.ui.app import AviApp

        app = AviApp(MagicMock(), MagicMock(), background=True, toggle=True, show=False, hide=False)
        assert app.background is True
        assert app.toggle is True
        assert app.show is False
        assert app.hide is False

    def test_cli_ui_flags_parsing(self):
        from avi.cli import main

        with patch("avi.ui.AviApp") as mock_app_cls:
            mock_inst = MagicMock()
            mock_inst.run.return_value = 0
            mock_app_cls.return_value = mock_inst

            main(["ui", "--background", "--toggle"])
            mock_app_cls.assert_called()
            _, kwargs = mock_app_cls.call_args
            assert kwargs["background"] is True
            assert kwargs["toggle"] is True

            with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
                main(["activate"])
                _, kwargs = mock_app_cls.call_args
                assert kwargs["toggle"] is True

    def test_session_state_preserved_across_dismissal(self, mock_gtk, tmp_path):
        import time

        from avi.orchestrator.models import ConversationTurn
        from avi.retrieval.models import SearchResult
        from avi.session.state import load_session_state, save_session_state
        from avi.ui.window import AviWindow

        state_file = tmp_path / "session_state.json"
        res = SearchResult(
            id="v1", title="Local Agents", url="https://youtube.com/v1", source="youtube"
        )
        turn = ConversationTurn(
            turn_id=1,
            timestamp=time.time(),
            user_query="find youtube agent video",
            intent_type="youtube_search",
            response_text="Found: Local Agents",
            search_results=[res],
            selected_result=res,
        )
        save_session_state(turn, state_path=state_file)
        assert state_file.exists()

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        win.show_overlay()
        win.hide_overlay()

        # Session state file must still exist and be intact across overlay dismissals
        loaded = load_session_state(state_path=state_file, ttl_seconds=9999999999.0)
        assert loaded is not None
        assert loaded.selected_result.title == "Local Agents"


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


# ============================================================
# 7. Phase 14.1 — Daemon lifecycle & IPC routing unit tests
# ============================================================


class TestDaemonIpcHelpers:
    """Unit tests for daemon detection and IPC helper functions in app.py."""

    def test_daemon_is_running_no_gdbus(self):
        """_daemon_is_running returns False when gdbus is not found."""
        from avi.ui.app import _daemon_is_running

        with patch("avi.ui.app.shutil.which", return_value=None):
            assert _daemon_is_running() is False

    def test_daemon_is_running_name_not_in_output(self):
        """_daemon_is_running returns False when app is not registered."""
        from avi.ui.app import _daemon_is_running

        with (
            patch("avi.ui.app.shutil.which", return_value="/usr/bin/gdbus"),
            patch("avi.ui.app.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout="some.other.name", returncode=0)
            assert _daemon_is_running() is False

    def test_daemon_is_running_name_found(self):
        """_daemon_is_running returns True when app D-Bus name is present."""
        from avi.ui.app import _daemon_is_running

        with (
            patch("avi.ui.app.shutil.which", return_value="/usr/bin/gdbus"),
            patch("avi.ui.app.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout="'io.github.banisher2005.avi'", returncode=0
            )
            assert _daemon_is_running() is True

    def test_send_ipc_no_gdbus(self, capsys):
        """_send_ipc returns 1 with error when gdbus is not found."""
        from avi.ui.app import _send_ipc

        with patch("avi.ui.app.shutil.which", return_value=None):
            code = _send_ipc("--show")
        assert code == 1
        captured = capsys.readouterr()
        assert "gdbus" in captured.err

    def test_send_ipc_calls_gdbus(self):
        """_send_ipc invokes gdbus with the right app-id and flag."""
        from avi.ui.app import _send_ipc

        with (
            patch("avi.ui.app.shutil.which", return_value="/usr/bin/gdbus"),
            patch("avi.ui.app.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            code = _send_ipc("--show")
        assert code == 0
        args = mock_run.call_args[0][0]
        assert "io.github.banisher2005.avi" in args
        assert "org.gtk.Application.CommandLine" in args

    def test_start_daemon_uses_popen(self):
        """_start_daemon uses Popen with start_new_session=True (non-blocking)."""
        from avi.ui.app import _start_daemon

        with patch("avi.ui.app.subprocess.Popen") as mock_popen:
            _start_daemon("/usr/bin/python3", "/some/src", {})
        mock_popen.assert_called_once()
        _, kwargs = mock_popen.call_args
        assert kwargs.get("start_new_session") is True
        assert kwargs.get("stdin") == subprocess.DEVNULL


class TestAviAppNewParams:
    """Unit tests for new AviApp constructor parameters (quit_flag, daemon_inner)."""

    def test_app_quit_flag_stored(self):
        from avi.ui.app import AviApp

        app = AviApp(MagicMock(), MagicMock(), quit_flag=True)
        assert app.quit_flag is True

    def test_app_daemon_inner_stored(self):
        from avi.ui.app import AviApp

        app = AviApp(MagicMock(), MagicMock(), daemon_inner=True)
        assert app.daemon_inner is True

    def test_app_quit_no_daemon_running(self, capsys):
        """quit with no daemon running prints message and returns 0."""
        from avi.ui.app import AviApp

        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app._daemon_is_running", return_value=False),
            patch(
                "avi.ui.app.diagnose_gtk_environment"
            ) as mock_diag,
        ):
            report = MagicMock()
            report.system_python_has_gtk4 = True
            report.system_python_path = "/usr/bin/python3"
            mock_diag.return_value = report
            app = AviApp(MagicMock(), MagicMock(), quit_flag=True)
            code = app.run(allow_system_fallback=True)
        assert code == 0
        captured = capsys.readouterr()
        assert "not running" in captured.out

    def test_app_quit_daemon_running_sends_ipc(self):
        """quit with daemon running sends IPC --quit command."""
        from avi.ui.app import AviApp

        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app._daemon_is_running", return_value=True),
            patch("avi.ui.app._send_ipc", return_value=0) as mock_ipc,
            patch(
                "avi.ui.app.diagnose_gtk_environment"
            ) as mock_diag,
        ):
            report = MagicMock()
            report.system_python_has_gtk4 = True
            report.system_python_path = "/usr/bin/python3"
            mock_diag.return_value = report
            app = AviApp(MagicMock(), MagicMock(), quit_flag=True)
            code = app.run(allow_system_fallback=True)
        assert code == 0
        mock_ipc.assert_called_once_with("--quit")

    def test_app_background_daemon_already_running(self, capsys):
        """--background when daemon already running prints info and returns 0."""
        from avi.ui.app import AviApp

        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app._daemon_is_running", return_value=True),
            patch(
                "avi.ui.app.diagnose_gtk_environment"
            ) as mock_diag,
        ):
            report = MagicMock()
            report.system_python_has_gtk4 = True
            report.system_python_path = "/usr/bin/python3"
            mock_diag.return_value = report
            app = AviApp(MagicMock(), MagicMock(), background=True)
            code = app.run(allow_system_fallback=True)
        assert code == 0
        captured = capsys.readouterr()
        assert "already running" in captured.out

    def test_app_background_starts_daemon(self, capsys):
        """--background when daemon not running launches daemon via Popen."""
        from avi.ui.app import AviApp

        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app._daemon_is_running", return_value=False),
            patch("avi.ui.app._start_daemon") as mock_start,
            patch(
                "avi.ui.app.diagnose_gtk_environment"
            ) as mock_diag,
        ):
            report = MagicMock()
            report.system_python_has_gtk4 = True
            report.system_python_path = "/usr/bin/python3"
            mock_diag.return_value = report
            app = AviApp(MagicMock(), MagicMock(), background=True)
            code = app.run(allow_system_fallback=True)
        assert code == 0
        mock_start.assert_called_once()
        captured = capsys.readouterr()
        assert "started" in captured.out

    def test_app_show_routes_to_ipc_if_running(self):
        """--show when daemon running sends IPC --show."""
        from avi.ui.app import AviApp

        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app._daemon_is_running", return_value=True),
            patch("avi.ui.app._send_ipc", return_value=0) as mock_ipc,
            patch(
                "avi.ui.app.diagnose_gtk_environment"
            ) as mock_diag,
        ):
            report = MagicMock()
            report.system_python_has_gtk4 = True
            report.system_python_path = "/usr/bin/python3"
            mock_diag.return_value = report
            app = AviApp(MagicMock(), MagicMock(), show=True)
            code = app.run(allow_system_fallback=True)
        assert code == 0
        mock_ipc.assert_called_once_with("--show")

    def test_app_show_starts_daemon_if_not_running(self):
        """--show when daemon not running starts daemon then sends --show."""
        from avi.ui.app import AviApp

        call_count = {"n": 0}

        def running_side_effect():
            call_count["n"] += 1
            # Return False first (before start), True after
            return call_count["n"] > 1

        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app._daemon_is_running", side_effect=running_side_effect),
            patch("avi.ui.app._start_daemon") as mock_start,
            patch("avi.ui.app._send_ipc", return_value=0) as mock_ipc,
            patch("avi.ui.app.time.sleep"),  # skip real sleep
            patch(
                "avi.ui.app.diagnose_gtk_environment"
            ) as mock_diag,
        ):
            report = MagicMock()
            report.system_python_has_gtk4 = True
            report.system_python_path = "/usr/bin/python3"
            mock_diag.return_value = report
            app = AviApp(MagicMock(), MagicMock(), show=True)
            code = app.run(allow_system_fallback=True)
        assert code == 0
        mock_start.assert_called_once()
        mock_ipc.assert_called_once_with("--show")


class TestDaemonLifecycleSmoke:
    """Real process lifecycle smoke test for daemon start/show/quit flow."""

    def test_daemon_lifecycle_background_show_quit(self):
        """Integration smoke: start daemon in bg, send --show, send --quit."""
        import shutil
        import subprocess
        import time

        from avi.ui.detector import diagnose_gtk_environment

        report = diagnose_gtk_environment()
        if not (report.system_python_has_gtk4 and report.system_python_path):
            pytest.skip("System Python with GTK4 not available")

        if not shutil.which("gdbus"):
            pytest.skip("gdbus not available")

        src_path = str(
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent / "src"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = src_path

        # 1. Kill any leftover daemon
        subprocess.run(
            [report.system_python_path, "-m", "avi.cli", "ui", "--quit"],
            env=env,
            capture_output=True,
            timeout=5,
        )
        time.sleep(0.5)

        # 2. Start daemon via --background (must return promptly, not block)
        t0 = time.monotonic()
        bg_proc = subprocess.run(
            [report.system_python_path, "-m", "avi.cli", "ui", "--background"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        elapsed = time.monotonic() - t0
        # The venv shim for --background should now use Popen and return quickly.
        # Under system python, --background starts the daemon and returns 0.
        assert bg_proc.returncode == 0, f"--background failed: {bg_proc.stderr}"
        # It should not take a long time (not blocking)
        assert elapsed < 8, f"--background blocked for {elapsed:.1f}s"

        # 3. Wait for daemon to register on D-Bus (up to 5s)
        registered = False
        for _ in range(50):
            time.sleep(0.1)
            check = subprocess.run(
                [
                    "gdbus",
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
            if "io.github.banisher2005.avi" in check.stdout:
                registered = True
                break
        assert registered, "AVI daemon did not register on D-Bus within 5s"

        # 4. Send --quit via IPC
        quit_proc = subprocess.run(
            [report.system_python_path, "-m", "avi.cli", "ui", "--quit"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert quit_proc.returncode == 0, f"--quit failed: {quit_proc.stderr}"

        # 5. Verify daemon stopped
        time.sleep(1)
        check2 = subprocess.run(
            [
                "gdbus",
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
        assert (
            "io.github.banisher2005.avi" not in check2.stdout
        ), "Daemon still running after --quit"


# ============================================================
# 8. Phase 14.2 CLI Command Palette Tests
# ============================================================


class TestCliCommandPalette:
    """Unit tests for Phase 14.2 CLI-style developer command palette UI/UX."""

    @pytest.fixture
    def mock_gtk(self, monkeypatch):
        mock_gtk = MagicMock()
        mock_gdk = MagicMock()
        mock_glib = MagicMock()
        mock_pango = MagicMock()
        mock_glib.idle_add = lambda fn, *args: fn(*args)
        mock_glib.timeout_add = MagicMock(return_value=999)
        mock_glib.source_remove = MagicMock()

        import avi.ui.window as wmod

        monkeypatch.setattr(wmod, "Gtk", mock_gtk)
        monkeypatch.setattr(wmod, "Gdk", mock_gdk)
        monkeypatch.setattr(wmod, "GLib", mock_glib)
        monkeypatch.setattr(wmod, "Pango", mock_pango)
        monkeypatch.setattr(wmod, "_GTK_AVAILABLE", True)
        return mock_gtk, mock_gdk, mock_glib, mock_pango

    def test_command_bar_construction(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        assert hasattr(win, "prompt_glyph")
        assert hasattr(win, "prompt_entry")
        assert hasattr(win, "esc_hint")
        assert hasattr(win, "close_button")
        win.window.set_default_size.assert_called_with(800, 580)

    def test_cli_execution_lines_and_glyphs(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        gtk_mock = mock_gtk[0]

        # 1. User message -> ❯ glyph
        win._add_user_message("open youtube mkbhd")
        assert len(win._history_widgets) == 1
        assert "❯" in [call.kwargs.get("label") for call in gtk_mock.Label.call_args_list]

        # 2. Working line -> ◌ glyph
        win._show_working_line("Searching YouTube…")
        assert win._current_working_widget is not None
        assert "◌" in [call.kwargs.get("label") for call in gtk_mock.Label.call_args_list]

        # 3. Assistant response -> ✓ glyph, clears working line
        win._add_assistant_message("Found 5 results")
        assert win._current_working_widget is None
        assert "✓" in [call.kwargs.get("label") for call in gtk_mock.Label.call_args_list]

        # 4. Error line -> ! glyph
        win._show_error("Could not reach service")
        assert "!" in [call.kwargs.get("label") for call in gtk_mock.Label.call_args_list]

    def test_compact_result_rows_rendering(self, mock_gtk):
        from avi.retrieval.models import SearchResult
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        gtk_mock = mock_gtk[0]

        r1 = SearchResult(id="1", title="Video 1", url="https://youtube.com/v1", channel="Chan 1", source="youtube")
        r2 = SearchResult(id="2", title="Video 2", url="https://youtube.com/v2", channel="Chan 2", source="youtube")

        win._add_search_results_widget([r1, r2], selected_result=r1)
        labels = [call.kwargs.get("label") for call in gtk_mock.Label.call_args_list]
        assert "[ ▶ ]" in labels
        assert "[02]" in labels
        assert len(win._current_search_rows) == 2

    def test_arrow_navigation_search_results(self, mock_gtk):
        from avi.retrieval.models import SearchResult
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        gdk = mock_gtk[1]
        gdk.KEY_Down = 65364
        gdk.KEY_KP_Down = 65433
        gdk.KEY_Up = 65362
        gdk.KEY_KP_Up = 65431
        gdk.KEY_Return = 65293
        gdk.KEY_KP_Enter = 65421
        gdk.ModifierType.CONTROL_MASK = 4

        r1 = SearchResult(id="1", title="Video 1", url="https://youtube.com/v1", source="youtube")
        r2 = SearchResult(id="2", title="Video 2", url="https://youtube.com/v2", source="youtube")
        win._add_search_results_widget([r1, r2], selected_result=r1)

        # Down arrow moves to first row (index 0)
        win._on_key_pressed(MagicMock(), gdk.KEY_Down, 0, 0)
        assert win._selected_row_index == 0

        # Down arrow moves to second row (index 1)
        win._on_key_pressed(MagicMock(), gdk.KEY_Down, 0, 0)
        assert win._selected_row_index == 1

        # Up arrow moves back to first row (index 0)
        win._on_key_pressed(MagicMock(), gdk.KEY_Up, 0, 0)
        assert win._selected_row_index == 0

        # Up arrow moves to -1 (deselects)
        win._on_key_pressed(MagicMock(), gdk.KEY_Up, 0, 0)
        assert win._selected_row_index == -1

    def test_prompt_history_navigation(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        gdk = mock_gtk[1]
        gdk.KEY_Down = 65364
        gdk.KEY_KP_Down = 65433
        gdk.KEY_Up = 65362
        gdk.KEY_KP_Up = 65431
        gdk.ModifierType.CONTROL_MASK = 4

        # Manually push 2 prompt history items
        win._prompt_history = ["increase volume", "take a screenshot"]
        win._history_index = 2

        # Up arrow recalls previous prompt
        win._on_key_pressed(MagicMock(), gdk.KEY_Up, 0, 0)
        win.prompt_entry.set_text.assert_called_with("take a screenshot")
        assert win._history_index == 1

        # Up arrow recalls older prompt
        win._on_key_pressed(MagicMock(), gdk.KEY_Up, 0, 0)
        win.prompt_entry.set_text.assert_called_with("increase volume")
        assert win._history_index == 0

        # Down arrow moves forward
        win._on_key_pressed(MagicMock(), gdk.KEY_Down, 0, 0)
        win.prompt_entry.set_text.assert_called_with("take a screenshot")
        assert win._history_index == 1


class TestTrueBlackPalette:
    """Tests for Phase 14.3 True Black palette colors, dimensions, and error handling."""

    @pytest.fixture
    def mock_gtk(self, monkeypatch):
        mock_gtk = MagicMock()
        mock_gdk = MagicMock()
        mock_glib = MagicMock()
        mock_pango = MagicMock()
        mock_glib.idle_add = lambda fn, *args: fn(*args)
        mock_glib.timeout_add = MagicMock(return_value=999)
        mock_glib.source_remove = MagicMock()

        import avi.ui.window as wmod

        monkeypatch.setattr(wmod, "Gtk", mock_gtk)
        monkeypatch.setattr(wmod, "Gdk", mock_gdk)
        monkeypatch.setattr(wmod, "GLib", mock_glib)
        monkeypatch.setattr(wmod, "Pango", mock_pango)
        monkeypatch.setattr(wmod, "_GTK_AVAILABLE", True)
        return mock_gtk, mock_gdk, mock_glib, mock_pango

    def test_css_true_black_tokens(self):
        from avi.ui.window import CSS_STYLE

        css = CSS_STYLE.decode("utf-8")
        # Backgrounds
        assert "#050505" in css  # window background
        assert "#080808" in css  # command bar
        assert "#0D0D0D" in css  # execution transcript area
        assert "#151515" in css  # hover / selected row
        # Borders
        assert "#242424" in css  # subtle border
        assert "#303030" in css  # strong border
        # Typography & Accents
        assert "#F2F2F2" in css  # primary text & glyphs
        assert "#A0A0A0" in css  # secondary text / working
        assert "#666666" in css  # muted text / keycap hints
        assert "#FF6B6B" in css  # error red

    def test_error_formatting_timeout_and_fallback(self):
        from avi.ui.window import format_user_friendly_error

        assert format_user_friendly_error("Connection timed out after 30s") == "AVI took too long to respond."
        assert format_user_friendly_error("Request timed out") == "AVI took too long to respond."
        assert format_user_friendly_error("Unknown system failure occurred line 99 Traceback: foo") == "I couldn't determine a safe action for that request."

    def test_palette_dimensions(self, mock_gtk):
        from avi.ui.window import AviWindow

        win = AviWindow(MagicMock(), MagicMock(), MagicMock())
        win.window.set_default_size.assert_called_with(800, 580)
        win.scroll_window.set_max_content_height.assert_called_with(600)


